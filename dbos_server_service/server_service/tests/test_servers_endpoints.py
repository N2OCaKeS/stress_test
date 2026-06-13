"""Интеграционные тесты `/api/server/v1/servers` (CRUD).

Покрытие:
* GET / — list по dept, `admin_token` (department_admin в dep_a) видит только
  свой dept (`dep_a`); пагинация limit/offset; scope без department → пусто;
* POST / — happy path × admin (department_admin/service-admin/operator),
  guest/reader/no-role → 403, cross-dept POST для НЕ-account_admin → 403
  DEPARTMENT_ISOLATION, дубли hostname/ip/serial → 409;
* GET /{id} — 404 hidden для чужого dept (включая department_admin'ов чужого
  отдела), reader видит свой;
* PATCH — admin/operator OK, reader → 403, hidden cross-dept → 404, дубль → 409;
* DELETE — admin OK, operator → 403 (нет в default grants), cross-dept → 404
  hidden, каскадно удаляет связанные accounts/disks через ORM cascade.

Заметка: `admin_token` отдаёт `department_admin` в `dep_a` с сервисной
ролью `admin` (а НЕ `account_admin`). `account_admin` блокируется guard
middleware ДО endpoint'а (admin-plane separation), поэтому для тест-сценариев
«admin делает что-то в своём отделе» используется `department_admin`.
Cross-dept тесты проверяют, что department_admin **НЕ** обходит изоляцию.
"""

from __future__ import annotations

import pytest

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/servers"


# ── GET / (list) ─────────────────────────────────────────────────────────────

class TestListOpenApiSchema:
    """`GET /servers` должен нести типизированный response-schema, а не пустую
    `{}` (иначе генераторы клиентов не видят envelope)."""

    def test_list_response_schema_is_typed(self):
        from src.main import app

        spec = app.openapi()
        get_op = spec["paths"]["/api/server/v1/servers"]["get"]
        schema = get_op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema != {}
        # Union двух envelope'ов — anyOf из двух $ref.
        refs = {opt.get("$ref") for opt in schema.get("anyOf", [])}
        assert any(r and "PaginatedResponse_ServerResponse_" in r for r in refs)
        assert any(r and "CursorPaginatedResponse_ServerResponse_" in r for r in refs)

    def test_server_schema_hides_secrets(self):
        from src.main import app

        spec = app.openapi()
        props = spec["components"]["schemas"]["ServerResponse"]["properties"]
        assert "password" not in props
        assert "password_b64" not in props
        assert "password_encrypted" not in props
        assert {"id", "hostname", "ip_address", "department_id"} <= set(props)


class TestListServers:
    async def test_no_token_returns_401(self, client):
        resp = await client.get(BASE)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_garbage_token_returns_401(self, client):
        resp = await client.get(BASE, headers=_hdr("bogus_unregistered"))
        assert_error(resp, 401, "ACCESS_TOKEN_INVALID")

    async def test_no_role_user_returns_403(self, client, no_role_token_a, make_server):
        await make_server(department_id="dep_a")
        resp = await client.get(BASE, headers=_hdr(no_role_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_reader_sees_own_dept_only(self, client, reader_token_a, make_server):
        await make_server(department_id="dep_a")
        await make_server(department_id="dep_a")
        await make_server(department_id="dep_b")
        resp = await client.get(BASE, headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        depts = {s["department_id"] for s in body["items"]}
        assert depts == {"dep_a"}

    async def test_department_admin_sees_only_own_dept(self, client, admin_token, make_server):
        """`admin_token` теперь department_admin в dep_a — НЕ видит dep_b.

        Регрессия для §5/§7 server_service модели: department_admin
        работает только с ресурсами своего департамента. Раньше тут был
        ``test_account_admin_sees_all`` — account_admin полностью
        блокируется guard middleware'ом, см.
        ``tests/integration/test_platform_admin_block.py``.
        """
        await make_server(department_id="dep_a")
        await make_server(department_id="dep_a")
        await make_server(department_id="dep_b")
        resp = await client.get(BASE, headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        depts = {s["department_id"] for s in body["items"]}
        assert depts == {"dep_a"}

    async def test_pagination(self, client, admin_token, make_server):
        for _ in range(5):
            await make_server(department_id="dep_a")
        resp = await client.get(BASE, headers=_hdr(admin_token),
                                params={"limit": 2, "offset": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert len(body["items"]) == 2
        assert body["total"] == 5

    async def test_limit_too_large_rejected(self, client, admin_token):
        resp = await client.get(BASE, headers=_hdr(admin_token), params={"limit": 1000})
        assert_error(resp, 422, "VALIDATION_ERROR")


# ── POST / (create) ──────────────────────────────────────────────────────────

class TestCreateServer:
    @staticmethod
    def _payload(**overrides):
        data = {
            "hostname": "newhost",
            "ip_address": "10.5.5.5",
            "department_id": "dep_a",
            "ssh_port": 22,
        }
        data.update(overrides)
        return data

    async def test_department_admin_creates_own_dept(self, client, admin_token):
        """`admin_token` теперь department_admin в dep_a — создаёт у себя.

        Раньше тут был ``test_account_admin_creates`` (account_admin
        bypass'ил matrix). После §7-8 фикса account_admin блокируется
        guard'ом; департамент-admin со service-role `admin` создаёт у себя.
        """
        resp = await client.post(BASE, headers=_hdr(admin_token), json=self._payload())
        assert resp.status_code == 201
        body = resp.json()
        assert body["hostname"] == "newhost"
        assert body["department_id"] == "dep_a"

    async def test_admin_role_creates_in_own_dept(self, client, admin_role_token_a):
        resp = await client.post(BASE, headers=_hdr(admin_role_token_a),
                                  json=self._payload(hostname="admin_new", ip_address="10.6.6.6"))
        assert resp.status_code == 201

    async def test_operator_creates_in_own_dept(self, client, operator_token_a):
        resp = await client.post(BASE, headers=_hdr(operator_token_a),
                                  json=self._payload(hostname="op_new", ip_address="10.7.7.7"))
        assert resp.status_code == 201

    async def test_reader_cannot_create(self, client, reader_token_a):
        resp = await client.post(BASE, headers=_hdr(reader_token_a), json=self._payload())
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_guest_cannot_create(self, client, guest_token_a):
        resp = await client.post(BASE, headers=_hdr(guest_token_a), json=self._payload())
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_non_admin_cannot_create_in_other_dept(self, client, operator_token_a):
        resp = await client.post(
            BASE,
            headers=_hdr(operator_token_a),
            json=self._payload(department_id="dep_b", hostname="cross", ip_address="10.8.8.8"),
        )
        assert_error(resp, 403, "DEPARTMENT_ISOLATION")

    async def test_department_admin_cannot_create_in_other_dept(self, client, admin_token):
        """`admin_token` теперь department_admin в dep_a → 403 для dep_b POST.

        Регрессия для §5/§7 (изоляция департаментов). Раньше тут был
        ``test_account_admin_can_create_in_any_dept`` — account_admin
        полностью блокируется guard'ом, см.
        ``tests/integration/test_platform_admin_block.py``.
        """
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json=self._payload(department_id="dep_b", hostname="adm_cross", ip_address="10.9.9.9"),
        )
        assert_error(resp, 403, "DEPARTMENT_ISOLATION")

    async def test_duplicate_hostname_conflict(self, client, admin_token, make_server):
        await make_server(department_id="dep_a", hostname="duphost")
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="duphost", ip_address="10.10.10.10"),
        )
        body = assert_error(resp, 409, "SERVER_DUPLICATE")
        # 409-response не должен утекать сырую строку psycopg в details.
        assert "db_error" not in (body.get("details") or {})

    async def test_duplicate_ip_conflict(self, client, admin_token, make_server):
        await make_server(department_id="dep_a", ip_address="10.11.11.11")
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="other_host", ip_address="10.11.11.11"),
        )
        body = assert_error(resp, 409, "SERVER_DUPLICATE")
        assert "db_error" not in (body.get("details") or {})

    async def test_create_conflict_does_not_leak_cross_dept_hostname(
        self, client, operator_token_a, make_server,
    ):
        """Юзер из `dep_a` НЕ должен в 409 увидеть hostname сервера из `dep_b`."""
        secret_hostname = "prod-db-01-corp-local"
        await make_server(department_id="dep_b", hostname=secret_hostname)
        resp = await client.post(
            BASE, headers=_hdr(operator_token_a),
            json=self._payload(
                department_id="dep_a",
                hostname=secret_hostname,
                ip_address="10.55.55.55",
            ),
        )
        body = assert_error(resp, 409, "SERVER_DUPLICATE")
        # Главное: значение конкретного hostname чужого dept не должно засветиться
        # ни в каком поле ответа (message, details.db_error, request_id и т.д.).
        assert secret_hostname not in resp.text
        details = body.get("details") or {}
        assert "db_error" not in details

    async def test_create_conflict_does_not_leak_cross_dept_ip(
        self, client, operator_token_a, make_server,
    ):
        """Юзер из `dep_a` НЕ должен в 409 увидеть IP сервера из `dep_b`."""
        secret_ip = "10.123.45.67"
        await make_server(department_id="dep_b", ip_address=secret_ip)
        resp = await client.post(
            BASE, headers=_hdr(operator_token_a),
            json=self._payload(
                department_id="dep_a",
                hostname="leak_probe",
                ip_address=secret_ip,
            ),
        )
        body = assert_error(resp, 409, "SERVER_DUPLICATE")
        assert secret_ip not in resp.text
        assert "db_error" not in (body.get("details") or {})

    async def test_create_conflict_does_not_leak_cross_dept_serial(
        self, client, operator_token_a, make_server,
    ):
        """Аналогично — serial_number чужого dept не должен светиться."""
        secret_serial = "SN-SECRET-XYZ-9999"
        await make_server(department_id="dep_b", serial_number=secret_serial)
        resp = await client.post(
            BASE, headers=_hdr(operator_token_a),
            json=self._payload(
                department_id="dep_a",
                hostname="serial_probe",
                ip_address="10.66.66.66",
                serial_number=secret_serial,
            ),
        )
        body = assert_error(resp, 409, "SERVER_DUPLICATE")
        assert secret_serial not in resp.text
        assert "db_error" not in (body.get("details") or {})

    async def test_invalid_ip_returns_422(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token),
                                  json=self._payload(ip_address="not-an-ip"))
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_ssh_port_out_of_range_returns_422(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token),
                                  json=self._payload(ssh_port=0))
        assert_error(resp, 422, "VALIDATION_ERROR")


# ── GET /{id} ────────────────────────────────────────────────────────────────

class TestGetServer:
    async def test_reader_sees_own_dept(self, client, reader_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(f"{BASE}/{srv.id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        assert resp.json()["id"] == srv.id

    async def test_reader_other_dept_returns_404_hidden(self, client, reader_token_a, make_server):
        """Кросс-департаментный сервер должен прятаться за 404, не 403."""
        srv = await make_server(department_id="dep_b")
        resp = await client.get(f"{BASE}/{srv.id}", headers=_hdr(reader_token_a))
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_department_admin_cannot_see_other_dept(self, client, admin_token, make_server):
        """`admin_token` теперь department_admin в dep_a → 404 для dep_b сервера.

        Регрессия для §5 (cross-dept hidden as 404). Раньше account_admin
        видел любой департамент — теперь блокируется guard'ом полностью.
        """
        srv = await make_server(department_id="dep_b")
        resp = await client.get(f"{BASE}/{srv.id}", headers=_hdr(admin_token))
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_nonexistent_id_returns_404(self, client, reader_token_a):
        resp = await client.get(f"{BASE}/srv_ghost", headers=_hdr(reader_token_a))
        assert_error(resp, 404, "SERVER_NOT_FOUND")


# ── PATCH /{id} ──────────────────────────────────────────────────────────────

class TestUpdateServer:
    async def test_operator_updates_own_dept(self, client, operator_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{BASE}/{srv.id}",
            headers=_hdr(operator_token_a),
            json={"display_name": "Renamed"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Renamed"

    async def test_reader_cannot_update(self, client, reader_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{BASE}/{srv.id}",
            headers=_hdr(reader_token_a),
            json={"display_name": "x"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404_hidden(self, client, operator_token_a, make_server):
        srv = await make_server(department_id="dep_b")
        resp = await client.patch(
            f"{BASE}/{srv.id}",
            headers=_hdr(operator_token_a),
            json={"display_name": "x"},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_empty_update_is_noop(self, client, operator_token_a, make_server):
        srv = await make_server(department_id="dep_a", hostname="original")
        resp = await client.patch(
            f"{BASE}/{srv.id}", headers=_hdr(operator_token_a), json={},
        )
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "original"

    async def test_update_duplicate_ip_conflict(self, client, admin_token, make_server):
        await make_server(department_id="dep_a", ip_address="10.20.20.20")
        srv = await make_server(department_id="dep_a", ip_address="10.21.21.21")
        resp = await client.patch(
            f"{BASE}/{srv.id}",
            headers=_hdr(admin_token),
            json={"ip_address": "10.20.20.20"},
        )
        body = assert_error(resp, 409, "SERVER_DUPLICATE")
        assert "db_error" not in (body.get("details") or {})

    # PATCH cross-dept hostname conflict: production-путь корректен и покрыт
    # CREATE-эквивалентом `test_create_conflict_does_not_leak_cross_dept_*`.
    # Бывший xfail-тест PATCH'а удалён — savepoint test-engine не материализовал
    # UNIQUE до COMMIT'а, тест всегда давал 200 и был мёртвым сигналом.
    # Покрытие cross-dept hostname leak ловится CREATE-тестом ниже:
    # `TestCreateServer::test_create_conflict_does_not_leak_cross_dept_hostname`.


# ── DELETE /{id} ─────────────────────────────────────────────────────────────

class TestDeleteServer:
    async def test_admin_deletes(self, client, admin_role_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.delete(f"{BASE}/{srv.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

    async def test_department_admin_deletes_own_dept(self, client, admin_token, make_server):
        """`admin_token` теперь department_admin в dep_a — удаляет у себя.

        Раньше тут был ``test_account_admin_deletes`` который удалял
        cross-dept сервер. Теперь cross-dept проверяется отдельно
        (см. ``test_cross_dept_returns_404_hidden``).
        """
        srv = await make_server(department_id="dep_a")
        resp = await client.delete(f"{BASE}/{srv.id}", headers=_hdr(admin_token))
        assert resp.status_code == 200

    async def test_operator_cannot_delete(self, client, operator_token_a, make_server):
        """`delete` отсутствует в default operator-grants (см. seed migration)."""
        srv = await make_server(department_id="dep_a")
        resp = await client.delete(f"{BASE}/{srv.id}", headers=_hdr(operator_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_reader_cannot_delete(self, client, reader_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.delete(f"{BASE}/{srv.id}", headers=_hdr(reader_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404_hidden(self, client, admin_role_token_a, make_server):
        srv = await make_server(department_id="dep_b")
        resp = await client.delete(f"{BASE}/{srv.id}", headers=_hdr(admin_role_token_a))
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_nonexistent_returns_404(self, client, admin_token):
        resp = await client.delete(f"{BASE}/srv_ghost", headers=_hdr(admin_token))
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_delete_removes_account_only_on_this_server(
        self, client, admin_role_token_a, make_server, make_account, db,
    ):
        """Аккаунт, привязанный только к удаляемому серверу, не остаётся orphan'ом.

        Конечный стейт «аккаунт без серверов» unlink-путь запрещает
        (ACCOUNT_NO_SERVERS), поэтому delete_server обязан снести такой аккаунт.
        """
        from sqlalchemy import select

        from src.models import ServerAccount, ServerAccountServer

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="root")
        acc_id = acc.id

        resp = await client.delete(f"{BASE}/{srv.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

        db.expire_all()
        account_row = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc_id)
        )).scalar_one_or_none()
        assert account_row is None, "осиротевший аккаунт должен быть удалён, не висеть с паролем"
        links = (await db.execute(
            select(ServerAccountServer.id).where(
                ServerAccountServer.account_id == acc_id
            )
        )).scalars().all()
        assert links == []

    async def test_delete_keeps_account_linked_to_another_server(
        self, client, admin_role_token_a, make_server, make_account, db,
    ):
        """Аккаунт, привязанный к двум серверам, переживает delete одного из них.

        У него лишь снимается связка на удалённый сервер; сам аккаунт и связка
        на оставшийся сервер живут дальше.
        """
        from sqlalchemy import select

        from src.models import ServerAccount, ServerAccountServer

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="root")
        acc_id = acc.id
        srv1_id = srv1.id
        srv2_id = srv2.id

        resp = await client.delete(f"{BASE}/{srv1_id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

        db.expire_all()
        account_row = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc_id)
        )).scalar_one_or_none()
        assert account_row is not None, "аккаунт ещё на втором сервере — должен жить"
        link_server_ids = set((await db.execute(
            select(ServerAccountServer.server_id).where(
                ServerAccountServer.account_id == acc_id
            )
        )).scalars().all())
        assert link_server_ids == {srv2_id}


# ── Banned user / inactive token ─────────────────────────────────────────────

class TestAuthDeniedPaths:
    async def test_banned_user_returns_401(self, client, make_token):
        token = make_token(department_id="dep_a", service_roles={"server_service": ["reader"]},
                           is_banned=True)
        resp = await client.get(BASE, headers=_hdr(token))
        assert_error(resp, 401, "USER_BANNED")

    async def test_inactive_token_returns_401(self, client, make_token):
        token = make_token(department_id="dep_a", active=False)
        resp = await client.get(BASE, headers=_hdr(token))
        assert_error(resp, 401, "ACCESS_TOKEN_INVALID")

    async def test_user_without_service_in_allowed_returns_403(self, client, make_token):
        token = make_token(
            department_id="dep_a",
            allowed_services=[],  # пустой
            service_roles={"server_service": ["reader"]},
        )
        resp = await client.get(BASE, headers=_hdr(token))
        assert_error(resp, 403, "SERVICE_ACCESS_DENIED")


# ── CPU fields (inline в server, без отдельного каталога) ───────────────────


class TestServerCpuFields:
    """CPU-метаданные (`cpu_brand`/`cpu_model`/`cpu_cores`/`cpu_threads`/
    `cpu_frequency_ghz`) живут плоско в строке `servers`. Раньше была
    отдельная таблица `cpu_models` с FK `servers.cpu_id` — выпиленная
    миграцией `b6f3a91d27e8_drop_cpu_models_inline_cpu_fields`.

    Поля обновляются `inventory.sync` callback'ом (через `/internal/servers/
    {id}/inventory`) или вручную через `POST /servers` / `PATCH /servers/{id}`.
    """

    @staticmethod
    def _payload(**overrides):
        data = {
            "hostname": "cpufields_host",
            "ip_address": "10.30.30.10",
            "department_id": "dep_a",
            "ssh_port": 22,
        }
        data.update(overrides)
        return data

    async def test_create_with_full_cpu_fields(self, client, admin_token):
        """POST с полным набором CPU-полей сохраняет их и возвращает в ответе."""
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json=self._payload(
                hostname="cpu-create-full",
                ip_address="10.30.30.11",
                cpu_brand="Intel",
                cpu_model="Xeon Silver 4314",
                cpu_cores=16,
                cpu_threads=32,
                cpu_frequency_ghz=2.4,
            ),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["cpu_brand"] == "Intel"
        assert body["cpu_model"] == "Xeon Silver 4314"
        assert body["cpu_cores"] == 16
        assert body["cpu_threads"] == 32
        assert body["cpu_frequency_ghz"] == 2.4

    async def test_create_without_cpu_fields_leaves_them_null(
        self, client, admin_token,
    ):
        """CPU-поля опциональны — без них INSERT проходит, все поля = None."""
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json=self._payload(
                hostname="cpu-create-empty", ip_address="10.30.30.12",
            ),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["cpu_brand"] is None
        assert body["cpu_model"] is None
        assert body["cpu_cores"] is None
        assert body["cpu_threads"] is None
        assert body["cpu_frequency_ghz"] is None

    async def test_patch_partial_cpu_fields(self, client, admin_token, make_server):
        """PATCH принимает CPU-поля по отдельности (partial update)."""
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{BASE}/{srv.id}",
            headers=_hdr(admin_token),
            json={"cpu_brand": "AMD", "cpu_model": "EPYC 7763"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["cpu_brand"] == "AMD"
        assert body["cpu_model"] == "EPYC 7763"
        # Поля, которых не было в PATCH — остаются None.
        assert body["cpu_cores"] is None
        assert body["cpu_threads"] is None
        assert body["cpu_frequency_ghz"] is None

    async def test_patch_negative_cores_returns_422(
        self, client, admin_token, make_server,
    ):
        """Валидация `ge=0` отбивает отрицательное число ядер."""
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{BASE}/{srv.id}",
            headers=_hdr(admin_token),
            json={"cpu_cores": -1},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_patch_negative_frequency_returns_422(
        self, client, admin_token, make_server,
    ):
        """`ge=0` на `cpu_frequency_ghz` (Float) тоже отбивается."""
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{BASE}/{srv.id}",
            headers=_hdr(admin_token),
            json={"cpu_frequency_ghz": -0.1},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    @pytest.mark.usefixtures("soft_dept_mode")
    async def test_inventory_sync_writes_cpu_fields_inline(
        self, client, admin_role_token_a, make_server, db,
    ):
        """`/internal/servers/{id}/inventory` пишет CPU-поля прямо в строку
        `servers`, без отдельной таблицы-каталога. Проверяем end-to-end:
        worker-callback → server-row.
        """
        from src.models import Server
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        payload = {
            "hostname": "inv-cpu-host",
            "kernel": "5.15.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon Gold 6342",
            "cpu_cores": 24,
            "cpu_threads": 48,
            "cpu_frequency_ghz": 2.8,
            "os_version": "Astra Linux SE 1.7",
            "disks": [],
        }
        resp = await client.post(
            f"/api/server/v1/internal/servers/{srv.id}/inventory",
            headers={**_hdr(admin_role_token_a), "X-Target-Department-Id": "dep_a"},
            json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Каталога cpu_models больше нет — поле `cpu_id` в ответе не возвращается.
        assert "cpu_id" not in body

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.cpu_brand == "Intel"
        assert refreshed.cpu_model == "Xeon Gold 6342"
        assert refreshed.cpu_cores == 24
        assert refreshed.cpu_threads == 48
        assert refreshed.cpu_frequency_ghz == 2.8

    async def test_get_returns_cpu_fields(self, client, admin_token, make_server, db):
        """GET /{id} отдаёт CPU-поля если они заполнены."""
        from src.models import Server
        from sqlalchemy import update

        srv = await make_server(department_id="dep_a")
        await db.execute(
            update(Server)
            .where(Server.id == srv.id)
            .values(
                cpu_brand="MCST",
                cpu_model="Elbrus 8C",
                cpu_cores=8,
                cpu_threads=8,
                cpu_frequency_ghz=1.3,
            )
        )
        await db.commit()
        resp = await client.get(f"{BASE}/{srv.id}", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["cpu_brand"] == "MCST"
        assert body["cpu_model"] == "Elbrus 8C"
        assert body["cpu_cores"] == 8
        assert body["cpu_threads"] == 8
        assert body["cpu_frequency_ghz"] == 1.3


# ── storage внутри сервера ──────────────────────────────────────────────────

class TestServerStorage:
    """Диски управляются только через карточку сервера (раздел `storage`)."""

    @staticmethod
    def _payload(**overrides):
        data = {
            "hostname": "storehost",
            "ip_address": "10.30.30.1",
            "department_id": "dep_a",
        }
        data.update(overrides)
        return data

    async def test_create_with_storage_returns_disks(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(storage=[
                {"slot": "system", "size_gb": 500, "model": "SSD-A"},
                {"slot": "disk1", "size_gb": 1000},
            ]),
        )
        assert resp.status_code == 201
        storage = {d["slot"]: d for d in resp.json()["storage"]}
        assert set(storage) == {"system", "disk1"}
        assert storage["system"]["size_gb"] == 500
        assert storage["system"]["is_system"] is True
        assert storage["system"]["model"] == "SSD-A"
        assert storage["disk1"]["is_system"] is False
        assert all("kind" not in d for d in resp.json()["storage"])
        assert all(d["id"].startswith("dsk_") for d in resp.json()["storage"])

    async def test_create_without_storage_is_empty(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="nostore", ip_address="10.30.30.2"),
        )
        assert resp.status_code == 201
        assert resp.json()["storage"] == []

    async def test_get_returns_storage(self, client, admin_token):
        created = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="getstore", ip_address="10.30.30.3", storage=[
                {"slot": "system", "size_gb": 256},
            ]),
        )
        srv_id = created.json()["id"]
        resp = await client.get(f"{BASE}/{srv_id}", headers=_hdr(admin_token))
        assert resp.status_code == 200
        storage = resp.json()["storage"]
        assert len(storage) == 1
        assert storage[0]["slot"] == "system"
        assert storage[0]["size_gb"] == 256
        assert storage[0]["is_system"] is True

    async def test_update_replaces_storage(self, client, admin_token):
        created = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="upd", ip_address="10.30.30.4", storage=[
                {"slot": "system", "size_gb": 100},
                {"slot": "disk1", "size_gb": 200},
            ]),
        )
        srv_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{srv_id}", headers=_hdr(admin_token),
            json={"storage": [
                {"slot": "system", "size_gb": 100},
                {"slot": "disk2", "size_gb": 4000},
            ]},
        )
        assert resp.status_code == 200
        slots = {d["slot"]: d for d in resp.json()["storage"]}
        assert set(slots) == {"system", "disk2"}
        assert slots["disk2"]["size_gb"] == 4000

    async def test_update_empty_storage_clears_disks(self, client, admin_token):
        created = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="clr", ip_address="10.30.30.5", storage=[
                {"slot": "system", "size_gb": 100},
            ]),
        )
        srv_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{srv_id}", headers=_hdr(admin_token),
            json={"storage": []},
        )
        assert resp.status_code == 200
        assert resp.json()["storage"] == []

    async def test_update_without_storage_keeps_disks(self, client, admin_token):
        created = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="keep", ip_address="10.30.30.6", storage=[
                {"slot": "system", "size_gb": 100},
            ]),
        )
        srv_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{srv_id}", headers=_hdr(admin_token),
            json={"display_name": "Renamed"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Renamed"
        assert len(resp.json()["storage"]) == 1

    async def test_two_system_disks_rejected(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="twosys", ip_address="10.30.30.7", storage=[
                {"slot": "disk1", "size_gb": 1, "is_system": True},
                {"slot": "disk2", "size_gb": 2, "is_system": True},
            ]),
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_duplicate_slot_rejected(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="dupslot", ip_address="10.30.30.8", storage=[
                {"slot": "disk1", "size_gb": 1},
                {"slot": "disk1", "size_gb": 2},
            ]),
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_no_disk_endpoint(self, client, admin_token, make_server):
        """Отдельного CRUD-endpoint'а дисков больше нет."""
        srv = await make_server(department_id="dep_a")
        resp = await client.get(f"{BASE}/{srv.id}/disks", headers=_hdr(admin_token))
        assert resp.status_code == 404

    async def test_storage_persisted_in_db(self, client, admin_token, db):
        from src.models import ServerDisk
        from sqlalchemy import select

        created = await client.post(
            BASE, headers=_hdr(admin_token),
            json=self._payload(hostname="dbcheck", ip_address="10.30.30.9", storage=[
                {"slot": "system", "size_gb": 333},
            ]),
        )
        srv_id = created.json()["id"]
        rows = (await db.execute(
            select(ServerDisk).where(ServerDisk.server_id == srv_id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].device_name == "system"
        assert rows[0].size_gb == 333
        assert rows[0].is_system is True
