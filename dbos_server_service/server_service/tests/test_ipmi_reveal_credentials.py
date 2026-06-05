"""Тесты раскрытия BMC-пароля через GET карточки IPMI-контроллера.

Отдельной reveal-ручки больше нет: `password_b64` приходит прямо в
`GET /api/server/v1/servers/{id}/ipmi`, если вызывающий держит
`view_credentials` (по дефолту только `admin` + `worker_bot`). reader/operator
с `view`, но без `view_credentials`, получают карточку без пароля. Раскрытие
пишет CRITICAL-аудит `ipmi_controller.credentials_revealed`.
"""
from __future__ import annotations

import base64

import pytest

BASE = "/api/server/v1/servers"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


def _ipmi_url(server_id: str) -> str:
    return f"{BASE}/{server_id}/ipmi"


class TestGetControllerCredentials:
    async def test_admin_with_view_credentials_sees_password(
        self, client, admin_role_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(
            server_id=srv.id, username="root", password="dell-idrac-pw",
        )
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["username"] == "root"
        assert body["password_b64"] is not None
        assert base64.b64decode(body["password_b64"]).decode() == "dell-idrac-pw"
        assert "dell-idrac-pw" not in body["password_b64"]
        assert "password_encrypted" not in body

    async def test_operator_with_only_view_gets_no_password(
        self, client, operator_token_a, make_server, make_ipmi,
    ):
        """operator держит `view`, но не `view_credentials` — карточка без пароля."""
        srv = await make_server(department_id="dep_a")
        await make_ipmi(
            server_id=srv.id, username="bmc-admin", password="hidden-from-operator",
        )
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(operator_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "bmc-admin"
        assert body["password_b64"] is None
        assert "hidden-from-operator" not in resp.text

    async def test_reader_with_only_view_gets_no_password(
        self, client, reader_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(
            server_id=srv.id, username="bmc-admin", password="hidden-from-reader",
        )
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["password_b64"] is None
        assert "hidden-from-reader" not in resp.text

    async def test_worker_bot_with_view_credentials_sees_password(
        self, client, worker_bot_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="worker-sees-this")
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(worker_bot_token_a))
        assert resp.status_code == 200
        assert base64.b64decode(resp.json()["password_b64"]).decode() == "worker-sees-this"

    async def test_cross_dept_returns_404_hidden(
        self, client, operator_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_b")
        await make_ipmi(
            server_id=srv.id, username="other-dept", password="cross-dept",
        )
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(operator_token_a))
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_no_ipmi_returns_404(
        self, client, operator_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")  # без IPMI controller
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(operator_token_a))
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(_ipmi_url(srv.id))
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_audit_emit_on_reveal_success(
        self, client, admin_role_token_a, make_server, make_ipmi, monkeypatch,
    ):
        """`ipmi_controller.credentials_revealed` пишется со status=success."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, "actor_id": actor_id, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.services.ipmi_controller.audit_service.emit", fake_emit,
        )

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(
            server_id=srv.id, username="bmc-audit", password="audit-leak-check",
        )
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

        reveals = [
            e for e in captured
            if e["action"] == "ipmi_controller.credentials_revealed"
        ]
        assert reveals, f"no credentials_revealed emit captured: {captured}"
        success = [e for e in reveals if e.get("status") == "success"]
        assert success, f"expected success emit, got {reveals}"
        emit = success[0]
        assert emit["target_id"] == ctrl.id
        assert emit["target_type"] == "ipmi_controller"
        assert emit["allowed"] is True
        details = emit.get("details") or {}
        assert "audit-leak-check" not in str(details)
        assert details.get("department_id") == "dep_a"
        assert details.get("server_id") == srv.id
        assert details.get("username") == "bmc-audit"

    async def test_no_reveal_audit_when_only_view(
        self, client, reader_token_a, make_server, make_ipmi, monkeypatch,
    ):
        """reader без view_credentials не триггерит credentials_revealed."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, "actor_id": actor_id, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.services.ipmi_controller.audit_service.emit", fake_emit,
        )

        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="not-revealed")
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        assert not [
            e for e in captured
            if e["action"] == "ipmi_controller.credentials_revealed"
        ]

    async def test_broken_ciphertext_returns_422(
        self, client, admin_role_token_a, make_server, make_ipmi, db,
    ):
        """Сломанный ciphertext + view_credentials → DECRYPT_FAILED (http 422)."""
        from sqlalchemy import update

        from src.models import IpmiController

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id, password="will-be-broken")
        await db.execute(
            update(IpmiController)
            .where(IpmiController.id == ctrl.id)
            .values(password_encrypted="v2$AAAAAAAAAAAAAAAA$BBBBBBBBBBBBBBBBBBBBBB")
        )
        await db.flush()
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(admin_role_token_a))
        assert_error(resp, 422, "DECRYPT_FAILED")

    async def test_broken_ciphertext_invisible_to_reader(
        self, client, reader_token_a, make_server, make_ipmi, db,
    ):
        """reader без view_credentials не декодирует — битый ciphertext не 500."""
        from sqlalchemy import update

        from src.models import IpmiController

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id, password="will-be-broken")
        await db.execute(
            update(IpmiController)
            .where(IpmiController.id == ctrl.id)
            .values(password_encrypted="v2$AAAAAAAAAAAAAAAA$BBBBBBBBBBBBBBBBBBBBBB")
        )
        await db.flush()
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        assert resp.json()["password_b64"] is None

    @pytest.mark.parametrize("kind", ["idrac", "ilo", "ipmi", "redfish"])
    async def test_kind_variants_same_shape(
        self, client, admin_role_token_a, make_server, make_ipmi, kind,
    ):
        """password_b64 в карточке не зависит от kind BMC."""
        srv = await make_server(department_id="dep_a")
        await make_ipmi(
            server_id=srv.id, kind=kind, username=f"u_{kind}",
            password=f"pw_for_{kind}",
        )
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["username"] == f"u_{kind}"
        assert base64.b64decode(body["password_b64"]).decode() == f"pw_for_{kind}"

    async def test_view_credentials_action_checked_once(
        self, client, admin_role_token_a, make_server, make_ipmi, monkeypatch,
    ):
        """`get_controller` не должен дважды дёргать `has_action(VIEW_CREDENTIALS)`.

        Раньше второй вызов делался непосредственно перед `_reveal_controller_password`,
        дублируя ранний lookup. Теперь решение принимается по локальной переменной.
        """
        from src.core.constants import Action, EntityType
        from src.services import permissions

        calls: list[tuple[str, str]] = []
        original = permissions.has_action

        async def spy(db, identity, entity, action):
            calls.append((entity, action))
            return await original(db, identity, entity, action)

        monkeypatch.setattr(
            "src.services.ipmi_controller.permissions.has_action", spy,
        )

        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="single-check")
        resp = await client.get(_ipmi_url(srv.id), headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text

        creds_calls = [
            c for c in calls
            if c == (EntityType.IPMI_CONTROLLER, Action.VIEW_CREDENTIALS)
        ]
        assert len(creds_calls) == 1, (
            f"VIEW_CREDENTIALS должен проверяться ровно один раз, было: {calls}"
        )
