"""Интеграционные тесты инстанс-уровневого ACL `/resource-permissions`.

Точечные гранты роли на КОНКРЕТНЫЙ ресурс (server / server_account) поверх
тип-wide матрицы. Покрываем:

* grant/revoke/list (happy-path admin, идемпотентность, 404 на revoke-промах);
* валидацию (не-инстансное действие, неизвестный resource_type);
* propagate merge vs mirror для обоих resource_type;
* dept-isolation (ресурс чужого отдела → 404);
* видимость серверов по инстанс-грантам без тип-wide view;
* guest: видит метаданные серверов, но не чувствительное.
"""

from __future__ import annotations

from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402

BASE = "/api/server/v1/resource-permissions"
SERVERS = "/api/server/v1/servers"
ACCOUNTS = "/api/server/v1/server-accounts"
PERMS = "/api/server/v1/permissions"


def _res_url(resource_type: str, resource_id: str, role: str, action: str) -> str:
    return f"{BASE}/{resource_type}/{resource_id}/{role}/{action}"


# ── grant / revoke / list ────────────────────────────────────────────────────


class TestGrantRevokeList:
    async def test_admin_grants_and_lists_by_resource(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.put(
            _res_url("server", srv.id, "reader", "power_status"),
            headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resource_type"] == "server"
        assert body["resource_id"] == srv.id
        assert body["role"] == "reader"
        assert body["action"] == "power_status"
        assert body["department_id"] == "dep_a"

        lst = await client.get(
            f"{BASE}/by-resource/server/{srv.id}", headers=_hdr(admin_token),
        )
        assert lst.status_code == 200
        items = lst.json()["items"]
        assert lst.json()["total"] == len(items)
        assert any(
            i["role"] == "reader" and i["action"] == "power_status" for i in items
        )

    async def test_grant_is_idempotent(self, client, admin_token, make_server):
        srv = await make_server(department_id="dep_a")
        first = await client.put(
            _res_url("server", srv.id, "reader", "view"), headers=_hdr(admin_token),
        )
        second = await client.put(
            _res_url("server", srv.id, "reader", "view"), headers=_hdr(admin_token),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

    async def test_revoke_existing(self, client, admin_token, make_server):
        srv = await make_server(department_id="dep_a")
        await client.put(
            _res_url("server", srv.id, "reader", "view"), headers=_hdr(admin_token),
        )
        resp = await client.delete(
            _res_url("server", srv.id, "reader", "view"), headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text

    async def test_revoke_nonexistent_404(self, client, admin_token, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.delete(
            _res_url("server", srv.id, "reader", "view"), headers=_hdr(admin_token),
        )
        assert_error(resp, 404, "RESOURCE_PERMISSION_NOT_FOUND")

    async def test_list_by_role_filters(self, client, admin_token, make_server):
        srv = await make_server(department_id="dep_a")
        await client.put(
            _res_url("server", srv.id, "role_x", "view"), headers=_hdr(admin_token),
        )
        await client.put(
            _res_url("server", srv.id, "role_y", "view"), headers=_hdr(admin_token),
        )
        resp = await client.get(f"{BASE}/by-role/role_x", headers=_hdr(admin_token))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items
        assert all(i["role"] == "role_x" for i in items)

    async def test_list_by_role_resource_type_filter(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="svc")
        await client.put(
            _res_url("server", srv.id, "role_z", "view"), headers=_hdr(admin_token),
        )
        await client.put(
            _res_url("server_account", acc.id, "role_z", "view"),
            headers=_hdr(admin_token),
        )
        resp = await client.get(
            f"{BASE}/by-role/role_z?resource_type=server_account",
            headers=_hdr(admin_token),
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items
        assert all(i["resource_type"] == "server_account" for i in items)

    async def test_guest_cannot_list(self, client, guest_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            f"{BASE}/by-resource/server/{srv.id}", headers=_hdr(guest_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_reader_cannot_grant(self, client, reader_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.put(
            _res_url("server", srv.id, "reader", "view"), headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── validation ───────────────────────────────────────────────────────────────


class TestValidation:
    async def test_non_instance_action_422(self, client, admin_token, make_server):
        """`create` нельзя выдать инстанс-грантом → 422 ACTION_NOT_INSTANCE_GRANTABLE."""
        srv = await make_server(department_id="dep_a")
        resp = await client.put(
            _res_url("server", srv.id, "reader", "create"), headers=_hdr(admin_token),
        )
        assert_error(resp, 422, "ACTION_NOT_INSTANCE_GRANTABLE")

    async def test_unknown_resource_type_422(self, client, admin_token):
        resp = await client.put(
            _res_url("os_version", "ver_1", "reader", "view"),
            headers=_hdr(admin_token),
        )
        assert_error(resp, 422, "UNKNOWN_RESOURCE_TYPE")

    async def test_unknown_resource_type_on_list_422(self, client, admin_token):
        resp = await client.get(
            f"{BASE}/by-resource/ipmi_controller/ipm_1", headers=_hdr(admin_token),
        )
        assert_error(resp, 422, "UNKNOWN_RESOURCE_TYPE")

    async def test_global_create_grant_via_permissions_still_works(
        self, client, admin_token,
    ):
        """`create` остаётся в глобальном слое — старый `/permissions` PUT 200."""
        first = await client.put(
            f"{PERMS}/server/operator/create", headers=_hdr(admin_token),
        )
        assert first.status_code == 200, first.text
        # Идемпотентно.
        second = await client.put(
            f"{PERMS}/server/operator/create", headers=_hdr(admin_token),
        )
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]


# ── dept-isolation ───────────────────────────────────────────────────────────


class TestDeptIsolation:
    async def test_grant_cross_dept_resource_404(
        self, client, admin_token, make_server,
    ):
        """dep_a admin не может грантовать на ресурс dep_b → 404 RESOURCE_NOT_FOUND."""
        srv_b = await make_server(department_id="dep_b")
        resp = await client.put(
            _res_url("server", srv_b.id, "reader", "view"), headers=_hdr(admin_token),
        )
        assert_error(resp, 404, "RESOURCE_NOT_FOUND")

    async def test_list_cross_dept_resource_hidden(
        self, client, admin_token, admin_token_b, make_server,
    ):
        """Грант создан в dep_b — dep_a admin его не видит в list по ресурсу."""
        srv_b = await make_server(department_id="dep_b")
        grant = await client.put(
            _res_url("server", srv_b.id, "reader", "view"), headers=_hdr(admin_token_b),
        )
        assert grant.status_code == 200, grant.text
        resp = await client.get(
            f"{BASE}/by-resource/server/{srv_b.id}", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["items"] == []


# ── propagate (server) ───────────────────────────────────────────────────────


def _pairs(items) -> set[tuple[str, str]]:
    return {(i["role"], i["action"]) for i in items}


class TestPropagateServer:
    async def _list(self, client, admin_token, resource_type, rid):
        resp = await client.get(
            f"{BASE}/by-resource/{resource_type}/{rid}", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200
        return resp.json()["items"]

    async def test_merge_adds_missing_keeps_existing(
        self, client, admin_token, make_server,
    ):
        src = await make_server(department_id="dep_a")
        t1 = await make_server(department_id="dep_a")
        t2 = await make_server(department_id="dep_a")
        # Образец: reader.view + reader.power_status.
        for action in ("view", "power_status"):
            await client.put(
                _res_url("server", src.id, "reader", action), headers=_hdr(admin_token),
            )
        # На t1 уже есть reader.view + лишний reader.console.
        await client.put(
            _res_url("server", t1.id, "reader", "view"), headers=_hdr(admin_token),
        )
        await client.put(
            _res_url("server", t1.id, "reader", "console"), headers=_hdr(admin_token),
        )

        resp = await client.post(
            f"{BASE}/server/{src.id}/propagate",
            headers=_hdr(admin_token),
            json={"target_resource_ids": [t1.id, t2.id], "mode": "merge"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source_grant_count"] == 2
        summary = {s["resource_id"]: s for s in body["targets"]}
        assert summary[t1.id]["added"] == 1 and summary[t1.id]["removed"] == 0
        assert summary[t2.id]["added"] == 2 and summary[t2.id]["removed"] == 0

        # t1: view + power_status + console (console не тронут merge'ом).
        t1_pairs = _pairs(await self._list(client, admin_token, "server", t1.id))
        assert t1_pairs == {
            ("reader", "view"), ("reader", "power_status"), ("reader", "console"),
        }
        t2_pairs = _pairs(await self._list(client, admin_token, "server", t2.id))
        assert t2_pairs == {("reader", "view"), ("reader", "power_status")}

    async def test_mirror_adds_and_removes(self, client, admin_token, make_server):
        src = await make_server(department_id="dep_a")
        t1 = await make_server(department_id="dep_a")
        for action in ("view", "power_status"):
            await client.put(
                _res_url("server", src.id, "reader", action), headers=_hdr(admin_token),
            )
        # t1: view + console (console лишний относительно образца).
        await client.put(
            _res_url("server", t1.id, "reader", "view"), headers=_hdr(admin_token),
        )
        await client.put(
            _res_url("server", t1.id, "reader", "console"), headers=_hdr(admin_token),
        )

        resp = await client.post(
            f"{BASE}/server/{src.id}/propagate",
            headers=_hdr(admin_token),
            json={"target_resource_ids": [t1.id], "mode": "mirror"},
        )
        assert resp.status_code == 200, resp.text
        summary = resp.json()["targets"][0]
        assert summary["added"] == 1  # power_status
        assert summary["removed"] == 1  # console

        t1_pairs = _pairs(await self._list(client, admin_token, "server", t1.id))
        assert t1_pairs == {("reader", "view"), ("reader", "power_status")}

    async def test_merge_idempotent(self, client, admin_token, make_server):
        src = await make_server(department_id="dep_a")
        t1 = await make_server(department_id="dep_a")
        await client.put(
            _res_url("server", src.id, "reader", "view"), headers=_hdr(admin_token),
        )
        payload = {"target_resource_ids": [t1.id], "mode": "merge"}
        first = await client.post(
            f"{BASE}/server/{src.id}/propagate", headers=_hdr(admin_token), json=payload,
        )
        assert first.json()["targets"][0]["added"] == 1
        second = await client.post(
            f"{BASE}/server/{src.id}/propagate", headers=_hdr(admin_token), json=payload,
        )
        assert second.json()["targets"][0]["added"] == 0
        assert second.json()["targets"][0]["removed"] == 0

    async def test_propagate_cross_dept_target_404(
        self, client, admin_token, make_server,
    ):
        src = await make_server(department_id="dep_a")
        t_b = await make_server(department_id="dep_b")
        await client.put(
            _res_url("server", src.id, "reader", "view"), headers=_hdr(admin_token),
        )
        resp = await client.post(
            f"{BASE}/server/{src.id}/propagate",
            headers=_hdr(admin_token),
            json={"target_resource_ids": [t_b.id], "mode": "merge"},
        )
        assert_error(resp, 404, "RESOURCE_NOT_FOUND")

    async def test_operator_cannot_propagate(
        self, client, operator_token_a, make_server, admin_token,
    ):
        """operator не имеет permission_grant → propagate отбит на ролевом gate."""
        src = await make_server(department_id="dep_a")
        t1 = await make_server(department_id="dep_a")
        await client.put(
            _res_url("server", src.id, "reader", "view"), headers=_hdr(admin_token),
        )
        resp = await client.post(
            f"{BASE}/server/{src.id}/propagate",
            headers=_hdr(operator_token_a),
            json={"target_resource_ids": [t1.id], "mode": "mirror"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── propagate (server_account) ───────────────────────────────────────────────


class TestPropagateAccount:
    async def _list(self, client, admin_token, rid):
        resp = await client.get(
            f"{BASE}/by-resource/server_account/{rid}", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200
        return resp.json()["items"]

    async def test_merge_and_mirror(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        src = await make_account(server_id=srv.id, login="src")
        t1 = await make_account(server_id=srv.id, login="t1")
        for action in ("view", "view_password"):
            await client.put(
                _res_url("server_account", src.id, "reader", action),
                headers=_hdr(admin_token),
            )
        # t1: view + лишний grant_sudo.
        await client.put(
            _res_url("server_account", t1.id, "reader", "view"),
            headers=_hdr(admin_token),
        )
        await client.put(
            _res_url("server_account", t1.id, "reader", "grant_sudo"),
            headers=_hdr(admin_token),
        )

        merge = await client.post(
            f"{BASE}/server_account/{src.id}/propagate",
            headers=_hdr(admin_token),
            json={"target_resource_ids": [t1.id], "mode": "merge"},
        )
        assert merge.status_code == 200, merge.text
        assert merge.json()["targets"][0]["added"] == 1  # view_password
        assert merge.json()["targets"][0]["removed"] == 0
        assert _pairs(await self._list(client, admin_token, t1.id)) == {
            ("reader", "view"), ("reader", "view_password"), ("reader", "grant_sudo"),
        }

        mirror = await client.post(
            f"{BASE}/server_account/{src.id}/propagate",
            headers=_hdr(admin_token),
            json={"target_resource_ids": [t1.id], "mode": "mirror"},
        )
        assert mirror.status_code == 200, mirror.text
        assert mirror.json()["targets"][0]["added"] == 0
        assert mirror.json()["targets"][0]["removed"] == 1  # grant_sudo
        assert _pairs(await self._list(client, admin_token, t1.id)) == {
            ("reader", "view"), ("reader", "view_password"),
        }


# ── visibility: instance grants extend server listing ────────────────────────


class TestServerVisibilityByGrant:
    async def test_custom_role_sees_exactly_granted_servers(
        self, client, admin_token, make_server, make_token,
    ):
        """Роль без тип-wide server.view видит ровно сервера с инстанс-грантом."""
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        await make_server(department_id="dep_a")  # без гранта — не виден
        for s in (srv1, srv2):
            g = await client.put(
                _res_url("server", s.id, "limited", "view"), headers=_hdr(admin_token),
            )
            assert g.status_code == 200, g.text

        token = make_token(
            department_id="dep_a", service_roles={"server_service": ["limited"]},
        )
        resp = await client.get(SERVERS, headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        ids = {i["id"] for i in resp.json()["items"]}
        assert ids == {srv1.id, srv2.id}

    async def test_custom_role_without_view_or_grant_403(
        self, client, make_server, make_token,
    ):
        await make_server(department_id="dep_a")
        token = make_token(
            department_id="dep_a", service_roles={"server_service": ["limited"]},
        )
        resp = await client.get(SERVERS, headers=_hdr(token))
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── guest: metadata visible, sensitive denied ────────────────────────────────


class TestGuestMetadataVsSensitive:
    async def test_guest_lists_servers(self, client, guest_token_a, make_server):
        """guest имеет тип-wide server.view → видит список серверов отдела."""
        srv = await make_server(department_id="dep_a")
        resp = await client.get(SERVERS, headers=_hdr(guest_token_a))
        assert resp.status_code == 200, resp.text
        ids = {i["id"] for i in resp.json()["items"]}
        assert srv.id in ids

    async def test_guest_sees_server_card(self, client, guest_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(f"{SERVERS}/{srv.id}", headers=_hdr(guest_token_a))
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == srv.id

    async def test_guest_denied_on_ipmi_credentials(
        self, client, guest_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{SERVERS}/{srv.id}/ipmi/credentials", headers=_hdr(guest_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_guest_denied_on_account_listing(self, client, guest_token_a):
        """guest не имеет server_account.view → список учёток 403."""
        resp = await client.get(ACCOUNTS, headers=_hdr(guest_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")
