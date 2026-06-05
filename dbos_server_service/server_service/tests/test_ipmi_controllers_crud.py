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
* POST /credentials/rotate — admin + worker_bot роль OK, operator OK
  (имеет rotate_credentials), reader → 403, plaintext не возвращается,
  password_rotated_at обновляется, можно подать конкретный пароль (worker
  callback path).
* boot/pxe/reinstall — dispatch'ат в worker через worker_client (тонкая
  обёртка), не часть CRUD-фокуса этого файла.
"""

from __future__ import annotations

import pytest

from src.services import secrets_service

BASE = "/api/server/v1/servers"
LIST = "/api/server/v1/ipmi_controllers"


from tests._helpers import auth_hdr as _hdr  # noqa: E402


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
                "password": "plain-secret-pw1",
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
                "password": "bmc-pass-1",
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
                "password": "rotated-plain-pwd-123",
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
                "password": "bmc-pass-1",
            },
        )
        assert resp.status_code == 403

    async def test_guest_cannot_create(self, client, guest_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(guest_token_a),
            json={
                "kind": "ipmi",
                "endpoint_url": "https://x",
                "username": "u",
                "password": "bmc-pass-1",
            },
        )
        assert resp.status_code == 403

    async def test_cross_dept_returns_404(self, client, operator_token_b, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(operator_token_b),
            json={
                "kind": "idrac",
                "endpoint_url": "https://x",
                "username": "u",
                "password": "bmc-pass-1",
            },
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "SERVER_NOT_FOUND"

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
                "password": "bmc-pass-1",
            },
        )
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "IPMI_DUPLICATE"

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            json={"kind": "idrac", "endpoint_url": "https://x", "username": "u", "password": "bmc-pass-1"},
        )
        assert resp.status_code == 401


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
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "NO_IPMI_CONTROLLER"

    async def test_cross_dept_returns_404(
        self, client, reader_token_b, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(f"{BASE}/{srv.id}/ipmi", headers=_hdr(reader_token_b))
        assert resp.status_code == 404
        # Cross-dept скрыт за тем же SERVER_NOT_FOUND, что и несуществующий —
        # иначе по разнице ответов можно перечислить чужие server_id
        assert resp.json().get("error_code") == "SERVER_NOT_FOUND"

    async def test_no_role_returns_403(self, client, no_role_token_a, make_server, make_ipmi):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(f"{BASE}/{srv.id}/ipmi", headers=_hdr(no_role_token_a))
        assert resp.status_code == 403


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
        assert resp.status_code == 403


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
        assert resp.status_code == 403

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
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "SERVER_NOT_FOUND"

    async def test_no_controller_returns_404(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={"username": "x"},
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "NO_IPMI_CONTROLLER"


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
        assert resp.status_code == 403

    async def test_cross_dept_returns_404(
        self, client, admin_token_b, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.delete(
            f"{BASE}/{srv.id}/ipmi", headers=_hdr(admin_token_b),
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "SERVER_NOT_FOUND"


# ── POST /credentials/rotate ────────────────────────────────────────────────


class TestRotateCredentials:
    """User-facing /credentials/rotate теперь 410 GONE.

    Endpoint писал произвольный plaintext в `password_encrypted` без BMC
    apply/verify — это могло разорвать out-of-band доступ. Корректный путь —
    worker-dispatch (`POST /ipmi-controllers/{id}/rotate`), который применяет
    пароль на BMC через Redfish/ipmitool и шлёт callback с verify-proof.
    Bot-callback оставлен как backwards-compat для уже задеплоенных worker'ов.
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
        assert resp.status_code == 410
        body = resp.json()
        assert body["error_code"] == "IPMI_ROTATE_USER_FACING_DEPRECATED"

    async def test_operator_gets_410(
        self, client, operator_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 410

    async def test_reader_gets_410(
        self, client, reader_token_a, make_server, make_ipmi,
    ):
        # reader всё равно user-facing — 410 раньше permission-check'а.
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 410

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
            assert resp.status_code == 410
            body = resp.json()
            details = body.get("details") or {}
            assert "server_id" not in details, (
                f"410 details echoed server_id for variant {variant!r}: {details!r}"
            )

    async def test_worker_bot_callback_still_allowed(
        self, client, worker_bot_token_a, make_server, make_ipmi, db,
    ):
        """Bot-callback (subject_type='bot') пока проходит для backwards-compat."""
        from src.models import IpmiController
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(worker_bot_token_a),
            json={"password": "applied-by-worker-via-redfish-1"},
        )
        assert resp.status_code == 200
        row = (
            await db.execute(
                select(IpmiController).where(IpmiController.server_id == srv.id)
            )
        ).scalar_one()
        assert secrets_service.decrypt(
            row.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(row.id),
        ) == "applied-by-worker-via-redfish-1"


# ── Парольная политика на create / rotate ────────────────────────────────────


class TestPasswordPolicy:
    """IPMI create и credentials/rotate с ручным паролем гейтятся политикой."""

    @pytest.mark.parametrize(
        "bad_password",
        ["short1", "nodigitshere", "12345678", "ab12"],
        ids=["too_short", "no_digit", "no_letter", "short_no_min"],
    )
    async def test_create_rejects_weak_password(
        self, client, admin_token, make_server, bad_password,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi",
            headers=_hdr(admin_token),
            json={
                "kind": "idrac",
                "endpoint_url": "https://idrac.example.com",
                "username": "u",
                "password": bad_password,
            },
        )
        assert resp.status_code == 422

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
                "password": "valid-pass-9",
            },
        )
        assert resp.status_code == 201

    @pytest.mark.parametrize(
        "bad_password",
        ["short1", "nodigitshere", "12345678"],
        ids=["too_short", "no_digit", "no_letter"],
    )
    async def test_rotate_rejects_weak_password(
        self, client, worker_bot_token_a, make_server, make_ipmi, bad_password,
    ):
        # User-facing rotate теперь 410, поэтому политика проверяется на
        # bot-callback пути (backwards-compat для уже задеплоенных worker'ов).
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(worker_bot_token_a),
            json={"password": bad_password},
        )
        assert resp.status_code == 422

    async def test_rotate_accepts_compliant_password(
        self, client, worker_bot_token_a, make_server, make_ipmi, db,
    ):
        from src.models import IpmiController
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(worker_bot_token_a),
            json={"password": "manual-rotate-7"},
        )
        assert resp.status_code == 200
        row = (
            await db.execute(
                select(IpmiController).where(IpmiController.server_id == srv.id)
            )
        ).scalar_one()
        assert secrets_service.decrypt(
            row.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(row.id),
        ) == "manual-rotate-7"

    async def test_rotate_without_body_generates(
        self, client, worker_bot_token_a, make_server, make_ipmi, db,
    ):
        from src.models import IpmiController
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id, password="initial-secret-1")
        original_encrypted = ctrl.password_encrypted
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200
        row = (
            await db.execute(
                select(IpmiController).where(IpmiController.server_id == srv.id)
            )
        ).scalar_one()
        assert row.password_encrypted != original_encrypted


# ── Audit emission ───────────────────────────────────────────────────────────


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает `audit_service.emit` для проверки action-key'ев и details."""
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr(
        "src.services.ipmi_controller.audit_service.emit", fake_emit,
    )
    return captured


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
                "password": "bmc-pass-1",
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
                "password": "bmc-pass-1",
            },
        )
        assert resp.status_code == 403
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

    async def test_rotate_emits_success(
        self, client, worker_bot_token_a, make_server, make_ipmi, captured_emits,
    ):
        # Через bot-callback путь — user-facing вызовы отбиваются 410 до
        # эмита success'а.
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200
        events = [
            e for e in _events(captured_emits, "ipmi_controller.rotate_credentials")
            if e.get("status") == "success"
        ]
        assert len(events) == 1
        ev = events[0]
        assert ev["target_id"] == ctrl.id
        assert ev["details"]["server_id"] == srv.id
        assert ev["details"]["reason"] == "user_initiated"
        assert ev["details"]["department_id"] == "dep_a"

    async def test_rotate_with_password_emits_worker_callback_reason(
        self, client, worker_bot_token_a, make_server, make_ipmi, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/credentials/rotate",
            headers=_hdr(worker_bot_token_a),
            json={"password": "applied-pw-1"},
        )
        assert resp.status_code == 200
        events = [
            e for e in _events(captured_emits, "ipmi_controller.rotate_credentials")
            if e.get("status") == "success"
        ]
        assert events[-1]["details"]["reason"] == "worker_callback"
