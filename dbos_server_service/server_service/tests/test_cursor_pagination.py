"""Интеграционные тесты cursor-пагинации list-эндпоинтов.

Покрытие:
* `/servers`, `/server-accounts`, `/os-versions`, `/ipmi_controllers` —
  cursor-режим включается `?cursor=true` (первая страница) или
  `?after=<token>` (следующие).
* Старый offset-режим продолжает работать без флагов — backwards-compat.
* Битый `after` → 400 INVALID_CURSOR.
* Department-isolation сохраняется в cursor-режиме (cross-dept не утекает).
* `limit + 1` row-fetch: при ровно-полной странице `has_more=False`.
"""

from __future__ import annotations

SERVERS = "/api/server/v1/servers"
ACCOUNTS = "/api/server/v1/server-accounts"
OS_VERSIONS = "/api/server/v1/os-versions"
IPMI = "/api/server/v1/ipmi_controllers"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


# ── /servers ─────────────────────────────────────────────────────────────────


class TestServersCursor:
    async def test_first_page_with_cursor_flag(
        self, client, admin_token, make_server,
    ):
        for _ in range(5):
            await make_server(department_id="dep_a")
        resp = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"cursor": "true", "limit": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {"items", "next_cursor", "has_more"}
        assert "total" not in body
        assert len(body["items"]) == 2
        assert body["has_more"] is True
        assert body["next_cursor"] is not None

    async def test_walking_pages_to_end_yields_all_items(
        self, client, admin_token, make_server,
    ):
        created = []
        for _ in range(5):
            created.append((await make_server(department_id="dep_a")).id)
        seen: list[str] = []
        cursor = None
        first = True
        # До 10 итераций — защита от бесконечного цикла, если что-то сломалось.
        for _ in range(10):
            params = {"limit": 2}
            if cursor is not None:
                params["after"] = cursor
            elif first:
                params["cursor"] = "true"
            first = False
            resp = await client.get(SERVERS, headers=_hdr(admin_token), params=params)
            assert resp.status_code == 200
            body = resp.json()
            seen.extend(item["id"] for item in body["items"])
            cursor = body["next_cursor"]
            if not body["has_more"]:
                break
        assert sorted(seen) == sorted(created)

    async def test_last_page_has_no_next_cursor(
        self, client, admin_token, make_server,
    ):
        for _ in range(3):
            await make_server(department_id="dep_a")
        resp = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"cursor": "true", "limit": 10},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_more"] is False
        assert body["next_cursor"] is None
        assert len(body["items"]) == 3

    async def test_exact_page_size_marks_no_more(
        self, client, admin_token, make_server,
    ):
        """При limit равном размеру датасета has_more=False (N+1-fetch не нашёл лишнего)."""
        for _ in range(3):
            await make_server(department_id="dep_a")
        resp = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"cursor": "true", "limit": 3},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 3
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    async def test_invalid_cursor_returns_400(
        self, client, admin_token,
    ):
        resp = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"after": "!!! not base64 !!!"},
        )
        assert_error(resp, 400, "INVALID_CURSOR")

    async def test_invalid_cursor_payload_returns_400(
        self, client, admin_token,
    ):
        # Валидный base64url, но не JSON. `aGk` → "hi".
        resp = await client.get(
            SERVERS, headers=_hdr(admin_token), params={"after": "aGk"},
        )
        assert_error(resp, 400, "INVALID_CURSOR")

    async def test_dept_isolation_in_cursor_mode(
        self, client, reader_token_a, make_server,
    ):
        await make_server(department_id="dep_a")
        await make_server(department_id="dep_a")
        await make_server(department_id="dep_b")
        await make_server(department_id="dep_b")
        resp = await client.get(
            SERVERS, headers=_hdr(reader_token_a), params={"cursor": "true"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert {s["department_id"] for s in body["items"]} == {"dep_a"}

    async def test_legacy_offset_mode_still_works(
        self, client, admin_token, make_server,
    ):
        """Без cursor=true и after — endpoint остаётся в offset-envelope'е."""
        for _ in range(3):
            await make_server(department_id="dep_a")
        resp = await client.get(
            SERVERS, headers=_hdr(admin_token),
            params={"limit": 2, "offset": 0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body
        assert body["total"] == 3
        assert "next_cursor" not in body


# ── /server-accounts ─────────────────────────────────────────────────────────


class TestServerAccountsCursor:
    async def test_first_page_cursor_envelope(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        for i in range(4):
            await make_account(server_id=srv.id, login=f"user{i}")
        resp = await client.get(
            ACCOUNTS, headers=_hdr(admin_token),
            params={"server_id": srv.id, "cursor": "true", "limit": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "next_cursor" in body
        assert "has_more" in body
        assert body["has_more"] is True
        assert len(body["items"]) == 2

    async def test_walk_with_after_collects_all(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        created = []
        for i in range(4):
            acc = await make_account(server_id=srv.id, login=f"u{i}")
            created.append(acc.id)
        seen = []
        cursor = None
        first = True
        for _ in range(10):
            params = {"server_id": srv.id, "limit": 2}
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

    async def test_invalid_cursor_returns_400(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            ACCOUNTS, headers=_hdr(admin_token),
            params={"server_id": srv.id, "after": "%%%"},
        )
        assert_error(resp, 400, "INVALID_CURSOR")

    async def test_cross_dept_server_hidden_in_cursor_mode(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        await make_account(server_id=srv.id, login="root")
        resp = await client.get(
            ACCOUNTS, headers=_hdr(reader_token_a),
            params={"server_id": srv.id, "cursor": "true"},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")


# ── /os-versions ─────────────────────────────────────────────────────────────


class TestOsVersionsCursor:
    async def test_cursor_envelope_first_page(
        self, client, admin_role_token_a,
    ):
        for i in range(4):
            resp = await client.post(
                OS_VERSIONS, headers=_hdr(admin_role_token_a),
                json={"name": f"osv-cursor-{i}"},
            )
            assert resp.status_code == 201
        resp = await client.get(
            OS_VERSIONS, headers=_hdr(admin_role_token_a),
            params={"cursor": "true", "limit": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "next_cursor" in body
        assert "has_more" in body
        assert len(body["items"]) == 2

    async def test_invalid_cursor_returns_400(self, client, admin_role_token_a):
        resp = await client.get(
            OS_VERSIONS, headers=_hdr(admin_role_token_a), params={"after": "###"},
        )
        assert_error(resp, 400, "INVALID_CURSOR")

    async def test_walk_collects_created_items(
        self, client, admin_role_token_a,
    ):
        created = []
        for i in range(5):
            resp = await client.post(
                OS_VERSIONS, headers=_hdr(admin_role_token_a),
                json={"name": f"osv-walk-{i}"},
            )
            created.append(resp.json()["id"])
        seen = []
        cursor = None
        first = True
        for _ in range(15):
            params: dict = {"limit": 2}
            if cursor is not None:
                params["after"] = cursor
            elif first:
                params["cursor"] = "true"
            first = False
            resp = await client.get(
                OS_VERSIONS, headers=_hdr(admin_role_token_a), params=params,
            )
            assert resp.status_code == 200
            body = resp.json()
            seen.extend(i["id"] for i in body["items"])
            cursor = body["next_cursor"]
            if not body["has_more"]:
                break
        # Все наши id должны попасть в seen (могут попасть и существующие
        # seed-версии, проверяем только подмножество).
        assert set(created).issubset(set(seen))


# ── /ipmi_controllers ────────────────────────────────────────────────────────


class TestIpmiControllersCursor:
    async def test_cursor_envelope_first_page(
        self, client, admin_token, make_server,
    ):
        for _ in range(3):
            await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.get(
            IPMI, headers=_hdr(admin_token),
            params={"cursor": "true", "limit": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "next_cursor" in body
        assert "has_more" in body
        assert body["has_more"] is True
        assert len(body["items"]) == 2

    async def test_invalid_cursor_returns_400(self, client, admin_token):
        resp = await client.get(
            IPMI, headers=_hdr(admin_token), params={"after": "@@@"},
        )
        assert_error(resp, 400, "INVALID_CURSOR")

    async def test_dept_isolation(
        self, client, reader_token_a, make_server,
    ):
        await make_server(department_id="dep_a", with_ipmi=True)
        await make_server(department_id="dep_b", with_ipmi=True)
        await make_server(department_id="dep_b", with_ipmi=True)
        resp = await client.get(
            IPMI, headers=_hdr(reader_token_a), params={"cursor": "true"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
