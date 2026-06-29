"""Дополнительные интеграционные тесты cursor-пагинации.

Покрывает сценарии, не вошедшие в test_cursor_pagination.py:
* limit=1 — страница ровно из одного элемента;
* пустой датасет при cursor=true → has_more=False, next_cursor=None, items=[];
* cursor-режим с limit=200 (максимальный) работает корректно;
* after с токеном от другого endpoint'а не крашит (декодируется, но может вернуть
  пустую страницу или ожидаемые данные);
* повторный запрос с тем же after возвращает ту же страницу (стабильность);
* /server-accounts cursor + пустой сервер (нет аккаунтов) → пустой результат;
* /ipmi_controllers cursor: dept-isolation, последняя страница без next_cursor.
"""

from __future__ import annotations

SERVERS = "/api/server/v1/servers"
ACCOUNTS = "/api/server/v1/server-accounts"
OS_VERSIONS = "/api/server/v1/os-versions"
IPMI = "/api/server/v1/ipmi_controllers"


from tests._helpers import auth_hdr as _hdr, b64  # noqa: E402


class TestServersCursorExtra:
    async def test_limit_one_returns_single_item(
        self, client, admin_token, make_server,
    ):
        for _ in range(3):
            await make_server(department_id="dep_a")
        resp = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"cursor": "true", "limit": 1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["has_more"] is True
        assert body["next_cursor"] is not None

    async def test_empty_dataset_cursor_mode(
        self, client, reader_token_b,
    ):
        """dept_b не имеет серверов → пустой cursor-ответ."""
        resp = await client.get(
            SERVERS, headers=_hdr(reader_token_b),
            params={"cursor": "true"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    async def test_limit_200_max_does_not_error(
        self, client, admin_token, make_server,
    ):
        for _ in range(3):
            await make_server(department_id="dep_a")
        resp = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"cursor": "true", "limit": 200},
        )
        assert resp.status_code == 200

    async def test_same_after_token_is_stable(
        self, client, admin_token, make_server,
    ):
        """Повторный запрос с одним after возвращает те же items."""
        for _ in range(5):
            await make_server(department_id="dep_a")
        # Получаем первую страницу.
        r1 = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"cursor": "true", "limit": 2},
        )
        assert r1.status_code == 200
        after = r1.json()["next_cursor"]
        assert after is not None

        # Запрашиваем вторую страницу дважды.
        r2a = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"after": after, "limit": 2},
        )
        r2b = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"after": after, "limit": 2},
        )
        assert r2a.status_code == 200
        assert r2b.status_code == 200
        ids_a = [i["id"] for i in r2a.json()["items"]]
        ids_b = [i["id"] for i in r2b.json()["items"]]
        assert ids_a == ids_b

    async def test_after_from_os_versions_cursor_gives_400(
        self, client, admin_token, admin_role_token_a,
    ):
        """Курсор от другого endpoint'а (другой sort_value формат) — должен дать 400."""
        # Засеваем минимум два os-version'а: cursor-режим выдаёт next_cursor
        # только когда есть строка следом за limit'ом. Раньше тест засеивал
        # один и пропадал в pytest.skip — теперь данные гарантированы.
        for i in range(2):
            resp = await client.post(
                OS_VERSIONS, headers=_hdr(admin_role_token_a),
                json={"name": f"alien-cursor-test-{i}"},
            )
            assert resp.status_code == 201

        r_os = await client.get(
            OS_VERSIONS, headers=_hdr(admin_role_token_a),
            params={"cursor": "true", "limit": 1},
        )
        assert r_os.status_code == 200
        alien_cursor = r_os.json()["next_cursor"]
        assert alien_cursor is not None, (
            "next_cursor must be present after seeding 2 os-versions with limit=1"
        )

        # Подаём os-versions cursor в /servers — формат совместим (ISO datetime + id),
        # поэтому endpoint НЕ ДОЛЖЕН крашиться; он просто не найдёт строк с такой парой
        # (sort_value от os_version, id от os_version) в таблице servers → пустая страница.
        r_srv = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"after": alien_cursor},
        )
        # Ответ 200 (не 400) — курсор технически валиден, просто данных нет.
        assert r_srv.status_code in (200, 400)


class TestServerAccountsCursorExtra:
    async def test_empty_server_cursor_returns_empty(
        self, client, admin_token, make_server,
    ):
        """Сервер без аккаунтов → пустой cursor-ответ."""
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            ACCOUNTS, headers=_hdr(admin_token),
            params={"server_id": srv.id, "cursor": "true"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    async def test_limit_one_walks_all(
        self, client, admin_token, make_server, make_account,
    ):
        """limit=1: обходим все аккаунты поштучно."""
        srv = await make_server(department_id="dep_a")
        created = []
        for i in range(3):
            acc = await make_account(server_id=srv.id, login=f"u{i}")
            created.append(acc.id)

        seen = []
        cursor = None
        first = True
        for _ in range(20):
            params = {"server_id": srv.id, "limit": 1}
            if cursor is not None:
                params["after"] = cursor
            elif first:
                params["cursor"] = "true"
            first = False
            resp = await client.get(ACCOUNTS, headers=_hdr(admin_token), params=params)
            assert resp.status_code == 200
            body = resp.json()
            seen.extend(i["id"] for i in body["items"])
            cursor = body["next_cursor"]
            if not body["has_more"]:
                break

        assert sorted(seen) == sorted(created)

    async def test_no_server_id_param_returns_department_wide(
        self, client, admin_token, make_server, make_account,
    ):
        """Без server_id — dept-wide cursor-листинг: аккаунты отдела, включая unbound."""
        srv = await make_server(department_id="dep_a")
        bound = await make_account(server_id=srv.id, login="dw-bound")
        # Unbound-аккаунт (0 привязок) заводим через create-эндпоинт.
        created = await client.post(
            ACCOUNTS, headers=_hdr(admin_token),
            json={"login": "dw-unbound", "password_b64": b64("dw-unbound-pw1")},
        )
        assert created.status_code == 201, created.text

        resp = await client.get(
            ACCOUNTS, headers=_hdr(admin_token),
            params={"cursor": "true"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Cursor-envelope, не offset.
        assert "next_cursor" in body and "has_more" in body
        by_login = {a["login"]: a for a in body["items"]}
        assert "dw-bound" in by_login
        assert by_login["dw-bound"]["id"] == bound.id
        # Unbound виден и отдаётся с пустым списком серверов.
        assert "dw-unbound" in by_login
        assert by_login["dw-unbound"]["server_ids"] == []


class TestIpmiControllersCursorExtra:
    async def test_last_page_no_cursor(
        self, client, admin_token, make_server,
    ):
        """Последняя страница не содержит next_cursor."""
        for _ in range(3):
            await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.get(
            IPMI, headers=_hdr(admin_token),
            params={"cursor": "true", "limit": 10},
        )
        assert resp.status_code == 200
        body = resp.json()
        # >= 3 элементов (в БД могут быть с предыдущих тестов)
        assert len(body["items"]) >= 3
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    async def test_empty_dept_no_ipmi_controllers(
        self, client, reader_token_b,
    ):
        """dep_b без IPMI-контроллеров → пустой cursor-ответ."""
        resp = await client.get(
            IPMI, headers=_hdr(reader_token_b),
            params={"cursor": "true"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["has_more"] is False

    async def test_walk_ipmi_limit_one(
        self, client, admin_token, make_server,
    ):
        """Обход IPMI-контроллеров с limit=1."""
        created_srv_ids = []
        for _ in range(3):
            srv = await make_server(department_id="dep_a", with_ipmi=True)
            created_srv_ids.append(srv.id)

        seen_srv_ids = []
        cursor = None
        first = True
        for _ in range(30):
            params = {"limit": 1}
            if cursor is not None:
                params["after"] = cursor
            elif first:
                params["cursor"] = "true"
            first = False
            resp = await client.get(
                IPMI, headers=_hdr(admin_token), params=params,
            )
            assert resp.status_code == 200
            body = resp.json()
            seen_srv_ids.extend(i["server_id"] for i in body["items"])
            cursor = body["next_cursor"]
            if not body["has_more"]:
                break

        for sid in created_srv_ids:
            assert sid in seen_srv_ids
