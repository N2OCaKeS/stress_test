"""Интеграционные тесты CRUD `/api/server/v1/servers/{server_id}/ipmi`
и `/api/server/v1/ipmi_controllers`.

Покрытие:

* POST / — happy path, шифрование пароля, dept-isolation (нельзя
  создать на чужой сервер), UNIQUE-конфликт (1:1), reader/guest → 403.
* GET / — owner видит, чужой dept → 404 SERVER_NOT_FOUND, без записи →
  404 NO_IPMI_CONTROLLER, audit на denied.
* GET (list /ipmi_controllers) — фильтр по dept, scope без department.
* PATCH / — частичное обновление, dept-isolation, без записи → 404.
* DELETE / — admin OK, operator → 403 (нет grant), cross-dept → 404.
* POST /credentials/rotate — 410 GONE для любого caller'а (включая bot).
  Endpoint писал ciphertext без BMC apply/verify; ротация теперь идёт только
  через worker dispatch + internal callback `credentials_rotated`.
* boot/pxe/reinstall — dispatch'ат в worker через worker_client (тонкая
  обёртка), не часть CRUD-фокуса этого файла.
"""

from __future__ import annotations

import pytest

from src.services import secrets_service

BASE = "/api/server/v1/servers"
LIST = "/api/server/v1/ipmi_controllers"


from tests._helpers import assert_error, auth_hdr as _hdr, b64  # noqa: E402


# ── POST /ipmi (create) ──────────────────────────────────────────────────────


class TestCreateController:
    async def test_admin_creates_controller(self, client, admin_token, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "idrac",
                "endpoint_url": "https://idrac.example.com",
                "username": "ipmi_admin",
                "password_b64": b64("plain-secret-pw1"),
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"].startswith("ipm_")
        assert body["server_id"] == srv.id
        assert body["kind"] == "idrac"
        assert body["endpoint_url"] == "https://idrac.example.com"
        assert body["username"] == "ipmi_admin"
        # plaintext-пароль не должен утечь в ответ ни в каком виде
        assert "password" not in body
        assert "password_encrypted" not in body

    async def test_operator_creates_controller(self, client, operator_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(operator_token_a),
            json={
                "kind": "redfish",
                "endpoint_url": "https://redfish.example.com",
                "username": "u",
                "password_b64": b64("bmc-pass-1"),
            },
        )
        assert resp.status_code == 201

    async def test_password_is_encrypted_in_db(
        self, client, admin_token, make_server, db,
    ):
        from src.models import IpmiController
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "ilo",
                "endpoint_url": "https://ilo.example.com",
                "username": "u",
                "password_b64": b64("rotated-plain-pwd-123"),
            },
        )
        row = (
            await db.execute(
                select(IpmiController).where(IpmiController.server_id == srv.id)
            )
        ).scalar_one()
        # password_encrypted формата `v<ver>$<nonce>$<ct>` — НЕ plaintext
        assert "rotated-plain-pwd-123" not in row.password_encrypted
        assert row.password_encrypted.startswith("v")
        # Раскрытие через secrets_service возвращает исходный plaintext
        assert secrets_service.decrypt(
            row.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(row.id),
        ) == "rotated-plain-pwd-123"

    async def test_reader_cannot_create(self, client, reader_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(reader_token_a),
            json={
                "kind": "ipmi",
                "endpoint_url": "https://x",
                "username": "u",
                "password_b64": b64("bmc-pass-1"),
            },
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_guest_cannot_create(self, client, guest_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(guest_token_a),
            json={
                "kind": "ipmi",
                "endpoint_url": "https://x",
                "username": "u",
                "password_b64": b64("bmc-pass-1"),
            },
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404(self, client, operator_token_b, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(operator_token_b),
            json={
                "kind": "idrac",
                "endpoint_url": "https://x",
                "username": "u",
                "password_b64": b64("bmc-pass-1"),
            },
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_duplicate_returns_409(self, client, admin_token, make_server, make_ipmi):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "redfish",
                "endpoint_url": "https://other.example.com",
                "username": "another",
                "password_b64": b64("bmc-pass-1"),
            },
        )
        assert_error(resp, 409, "IPMI_DUPLICATE")

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            json={"kind": "idrac", "endpoint_url": "https://x", "username": "u", "password_b64": b64("bmc-pass-1")},
        )
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")


# ── GET /ipmi (get one) ──────────────────────────────────────────────────────


class TestGetController:
    async def test_reader_can_view(self, client, reader_token_a, make_server, make_ipmi):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.get(f"{BASE}/{srv.id}/ipmi", headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == ctrl.id
        assert body["server_id"] == srv.id
        # plaintext НЕ возвращается
        assert "password" not in body
        assert "password_encrypted" not in body

    async def test_no_controller_returns_404(self, client, reader_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(f"{BASE}/{srv.id}/ipmi", headers=_hdr(reader_token_a))
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")

    async def test_cross_dept_returns_404(
        self, client, reader_token_b, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(f"{BASE}/{srv.id}/ipmi", headers=_hdr(reader_token_b))
        # Cross-dept скрыт за тем же SERVER_NOT_FOUND, что и несуществующий —
        # иначе по разнице ответов можно перечислить чужие server_id
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_no_role_returns_403(self, client, no_role_token_a, make_server, make_ipmi):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(f"{BASE}/{srv.id}/ipmi", headers=_hdr(no_role_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── GET /ipmi_controllers (list) ─────────────────────────────────────────────


class TestListControllers:
    async def test_reader_sees_only_own_dept(
        self, client, reader_token_a, make_server, make_ipmi,
    ):
        s1 = await make_server(department_id="dep_a")
        s2 = await make_server(department_id="dep_a")
        s3 = await make_server(department_id="dep_b")
        await make_ipmi(server_id=s1.id)
        await make_ipmi(server_id=s2.id)
        await make_ipmi(server_id=s3.id)
        resp = await client.get(LIST, headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        server_ids = {item["server_id"] for item in body["items"]}
        assert server_ids == {s1.id, s2.id}

    async def test_pagination(self, client, admin_token, make_server, make_ipmi):
        servers = [await make_server(department_id="dep_a") for _ in range(3)]
        for s in servers:
            await make_ipmi(server_id=s.id)
        resp = await client.get(
            LIST, headers=_hdr(admin_token), params={"limit": 2, "offset": 0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 0

    async def test_no_role_returns_403(self, client, no_role_token_a):
        resp = await client.get(LIST, headers=_hdr(no_role_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_list_returns_typed_envelope(
        self, client, reader_token_a, make_server, make_ipmi,
    ):
        """Offset-режим отдаёт {items, total, limit, offset}, карточка несёт
        ожидаемые поля контроллера и не светит зашифрованный пароль."""
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(LIST, headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"items", "total", "limit", "offset"}
        assert body["total"] == 1
        item = body["items"][0]
        assert {"id", "server_id", "kind", "endpoint_url", "username"} <= set(item)
        assert "password" not in item
        assert "password_encrypted" not in item

    async def test_list_cursor_returns_typed_envelope(
        self, client, reader_token_a, make_server, make_ipmi,
    ):
        """Cursor-режим отдаёт {items, next_cursor, has_more}."""
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            LIST, headers=_hdr(reader_token_a), params={"cursor": "true"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"items", "next_cursor", "has_more"}
        assert len(body["items"]) == 1


# ── OpenAPI-схема list-эндпоинта ──────────────────────────────────────────────


class TestListOpenApiSchema:
    """`GET /ipmi-controllers` должен нести типизированный response-schema,
    а не пустую `{}` (иначе генераторы клиентов не видят envelope)."""

    def test_list_response_schema_is_typed(self):
        from src.main import app

        spec = app.openapi()
        get_op = spec["paths"]["/api/server/v1/ipmi-controllers"]["get"]
        schema = get_op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema != {}
        # Union двух envelope'ов — anyOf из двух $ref.
        refs = {opt.get("$ref") for opt in schema.get("anyOf", [])}
        assert any(r and "PaginatedResponse_IpmiControllerResponse_" in r for r in refs)
        assert any(r and "CursorPaginatedResponse_IpmiControllerResponse_" in r for r in refs)

    def test_controller_schema_hides_encrypted_password(self):
        from src.main import app

        spec = app.openapi()
        props = spec["components"]["schemas"]["IpmiControllerResponse"]["properties"]
        assert "password_encrypted" not in props
        assert {"id", "server_id", "kind", "endpoint_url", "username"} <= set(props)


# ── PATCH /ipmi (update) ─────────────────────────────────────────────────────


class TestUpdateController:
    async def test_admin_updates_fields(
        self, client, admin_token, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.patch(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={"username": "new_user", "endpoint_url": "https://idrac-v2.example.com"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "new_user"
        assert body["endpoint_url"] == "https://idrac-v2.example.com"

    async def test_password_field_ignored_in_patch(
        self, client, admin_token, make_server, make_ipmi, db,
    ):
        """PATCH не принимает `password` — это поле не определено в схеме,
        Pydantic его игнорирует, password_encrypted остаётся прежним."""
        from src.models import IpmiController
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id, password="original-secret")
        original_encrypted = ctrl.password_encrypted

        resp = await client.patch(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={"username": "renamed", "password": "this-should-be-ignored"},
        )
        assert resp.status_code == 200
        row = (
            await db.execute(
                select(IpmiController).where(IpmiController.server_id == srv.id)
            )
        ).scalar_one()
        assert row.password_encrypted == original_encrypted

    async def test_reader_cannot_update(
        self, client, reader_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.patch(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(reader_token_a),
            json={"username": "x"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404(
        self, client, admin_token_b, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.patch(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token_b),
            json={"username": "x"},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_no_controller_returns_404(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={"username": "x"},
        )
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")


# ── DELETE /ipmi ─────────────────────────────────────────────────────────────


class TestDeleteController:
    async def test_admin_deletes(self, client, admin_token, make_server, make_ipmi):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.delete(
            f"{BASE}/{srv.id}/ipmi", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200
        # После удаления — повторный GET → 404 NO_IPMI_CONTROLLER
        get_resp = await client.get(
            f"{BASE}/{srv.id}/ipmi", headers=_hdr(admin_token),
        )
        assert get_resp.status_code == 404
        assert get_resp.json().get("error_code") == "NO_IPMI_CONTROLLER"

    async def test_operator_cannot_delete(
        self, client, operator_token_a, make_server, make_ipmi,
    ):
        """operator имеет view/create/update/rotate_credentials, но НЕ delete
        (см. migrations/831ba55543e9_seed_default_entity_permissions.py)."""
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.delete(
            f"{BASE}/{srv.id}/ipmi", headers=_hdr(operator_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404(
        self, client, admin_token_b, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.delete(
            f"{BASE}/{srv.id}/ipmi", headers=_hdr(admin_token_b),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")


# ── POST /credentials/rotate ────────────────────────────────────────────────


class TestRotateCredentials:
    """`/credentials/rotate` снят — 410 GONE для любого caller'а.

    Endpoint писал ciphertext в `password_encrypted` без BMC apply/verify.
    Любой держатель `(ipmi_controller, rotate_credentials)` — включая bot —
    мог разорвать out-of-band доступ к стойкам. Ротация теперь идёт только
    через `POST /ipmi-controllers/{id}/rotate` (worker dispatch → BMC apply →
    internal callback `credentials_rotated`, который делает verify-then-store).
    """

    async def test_admin_gets_410(
        self, client, admin_token, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="initial-secret")

        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(admin_token),
            json={},
        )
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")

    async def test_operator_gets_410(
        self, client, operator_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")

    async def test_reader_gets_410(
        self, client, reader_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")

    async def test_worker_bot_also_gets_410(
        self, client, worker_bot_token_a, make_server, make_ipmi, db,
    ):
        """Bot с `(ipmi_controller, rotate_credentials)` — тоже 410.

        Прошлый bot-fallback писал ciphertext без verify-proof; теперь
        BMC-apply гарантируется только через worker dispatch + internal
        callback. БД-ciphertext НЕ должен поменяться.
        """
        from src.models import IpmiController
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id, password="initial-secret")
        original_encrypted = ctrl.password_encrypted

        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(worker_bot_token_a),
            json={"password": "applied-by-worker-via-redfish-1"},
        )
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")

        row = (
            await db.execute(
                select(IpmiController).where(IpmiController.server_id == srv.id)
            )
        ).scalar_one()
        assert row.password_encrypted == original_encrypted

    async def test_410_details_do_not_echo_server_id(
        self, client, admin_token, make_server, make_ipmi,
    ):
        """410-ответ не эхо'ит server_id обратно.

        Caller мог передать `UPPER`/whitespace-варианты id'а — эхо такого
        значения создавало CAS-different отпечаток в ответе (полезной
        информации не несёт, audit и без того пишет сырой id).
        """
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)

        for variant in (srv.id.upper(), f"  {srv.id}  ", srv.id.lower()):
            resp = await client.post(
                f"{BASE}/{variant}/ipmi/credentials/rotate",
                headers=_hdr(admin_token),
                json={},
            )
            body = assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")
            details = body.get("details") or {}
            assert "server_id" not in details, (
                f"410 details echoed server_id for variant {variant!r}: {details!r}"
            )


# ── Приём существующего пароля BMC ──────────────────────────────────────────


class TestPasswordPolicy:
    """Пароль внешнего BMC принимается без локальной политики сложности."""

    @pytest.mark.parametrize(
        "existing_password",
        ["short1", "nodigitshere", "12345678", "ab12"],
        ids=["too_short", "no_digit", "no_letter", "short_no_min"],
    )
    async def test_create_accepts_existing_weak_password(
        self, client, admin_token, make_server, existing_password,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "idrac",
                "endpoint_url": "https://idrac.example.com",
                "username": "u",
                "password_b64": b64(existing_password),
            },
        )
        assert resp.status_code == 201, resp.text
        get = await client.get(f"{BASE}/{srv.id}/ipmi", headers=_hdr(admin_token))
        assert get.status_code == 200
        assert get.json()["password_b64"] == b64(existing_password)

    async def test_create_accepts_compliant_password(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "idrac",
                "endpoint_url": "https://idrac.example.com",
                "username": "u",
                "password_b64": b64("valid-pass-9"),
            },
        )
        assert resp.status_code == 201

    @pytest.mark.parametrize(
        "bad_b64",
        ["!!!notb64!!!", "aGVsbG8", "z===="],
        ids=["non_b64_chars", "bad_padding", "garbage"],
    )
    async def test_create_rejects_broken_base64(
        self, client, admin_token, make_server, bad_b64,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "idrac",
                "endpoint_url": "https://idrac.example.com",
                "username": "u",
                "password_b64": bad_b64,
            },
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_create_then_reveal_round_trips_password(
        self, client, admin_role_token_a, make_server, db,
    ):
        """password_b64 на create → reveal через GET декодится в исходный plaintext."""
        import base64

        srv = await make_server(department_id="dep_a")
        plaintext = "bmc-round-trip-9"
        create = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_role_token_a),
            json={
                "kind": "idrac",
                "endpoint_url": "https://idrac.example.com",
                "username": "u",
                "password_b64": b64(plaintext),
            },
        )
        assert create.status_code == 201, create.text
        get = await client.get(f"{BASE}/{srv.id}/ipmi", headers=_hdr(admin_role_token_a))
        assert get.status_code == 200
        revealed = get.json()["password_b64"]
        assert base64.b64decode(revealed).decode("utf-8") == plaintext

    # Парольная политика на rotate-route больше не применяется: endpoint снят
    # (410 GONE для любого caller'а), `IpmiCredentialsRotateRequest` тут не
    # парсится. Apply на BMC и валидация пароля идут в worker-handler через
    # `POST /ipmi-controllers/{id}/rotate`.


# ── Audit emission ───────────────────────────────────────────────────────────


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает `audit_service.emit` для проверки action-key'ев и details."""
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.services.ipmi_controller.audit_service.emit",
    )


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


class TestAuditEmission:
    async def test_create_emits_success(
        self, client, admin_token, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "idrac",
                "endpoint_url": "https://x",
                "username": "u",
                "password_b64": b64("bmc-pass-1"),
            },
        )
        assert resp.status_code == 201
        events = [
            e for e in _events(captured_emits, "ipmi_controller.create")
            if e.get("status") == "success"
        ]
        assert len(events) == 1
        ev = events[0]
        assert ev["target_type"] == "ipmi_controller"
        assert ev["allowed"] is True
        assert ev["details"]["server_id"] == srv.id
        assert ev["details"]["kind"] == "idrac"
        assert ev["details"]["department_id"] == "dep_a"

    async def test_create_denied_emits_audit(
        self, client, reader_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(reader_token_a),
            json={
                "kind": "idrac",
                "endpoint_url": "https://x",
                "username": "u",
                "password_b64": b64("bmc-pass-1"),
            },
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        denied = [
            e for e in _events(captured_emits, "ipmi_controller.create")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1
        assert denied[0]["allowed"] is False
        assert denied[0]["details"]["reason"] == "permission_denied"
        assert denied[0]["details"]["server_id"] == srv.id

    async def test_delete_emits_success(
        self, client, admin_token, make_server, make_ipmi, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.delete(
            f"{BASE}/{srv.id}/ipmi", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200
        events = [
            e for e in _events(captured_emits, "ipmi_controller.delete")
            if e.get("status") == "success"
        ]
        assert len(events) == 1
        ev = events[0]
        assert ev["target_id"] == ctrl.id
        assert ev["details"]["server_id"] == srv.id
        assert ev["details"]["department_id"] == "dep_a"

    async def test_rotate_emits_410_warning_for_user(
        self, client, admin_token, make_server, make_ipmi, monkeypatch,
    ):
        """Любой вызов rotate'а → WARNING audit `user_facing_endpoint_deprecated`."""
        from tests._helpers import make_emit_capture

        emits = make_emit_capture(
            monkeypatch,
            "src.api.v1.endpoints.ipmi.audit_service.emit",
        )
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(admin_token),
            json={},
        )
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")
        warn = [
            e for e in emits
            if e["action"] == "ipmi_controller.rotate_credentials"
            and e.get("status") == "warning"
        ]
        assert len(warn) == 1
        ev = warn[0]
        assert ev["allowed"] is False
        assert ev["details"]["reason"] == "user_facing_endpoint_deprecated"
        # caller_type — `identity.subject_type` (None для дефолтных user-токенов
        # без явного префикса, "bot" для PAT-ботов).
        assert ev["details"]["caller_type"] in (None, "user")
        assert ev["details"]["server_id"] == srv.id

    async def test_rotate_emits_410_warning_for_bot(
        self, client, worker_bot_token_a, make_server, make_ipmi, monkeypatch,
    ):
        """Bot тоже отбивается 410; в audit caller_type=bot."""
        from tests._helpers import make_emit_capture

        emits = make_emit_capture(
            monkeypatch,
            "src.api.v1.endpoints.ipmi.audit_service.emit",
        )
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(worker_bot_token_a),
            json={"password": "applied-by-worker-via-redfish-1"},
        )
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")
        warn = [
            e for e in emits
            if e["action"] == "ipmi_controller.rotate_credentials"
            and e.get("status") == "warning"
        ]
        assert len(warn) == 1
        assert warn[0]["details"]["caller_type"] == "bot"
