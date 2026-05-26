"""Тесты POST /api/server/v1/ipmi-controllers/{controller_id}/reveal-credentials.

Симметричный с `server-accounts/{id}/reveal-password` user-facing endpoint:
расшифровывает BMC-логин (plain) + пароль (base64). Дефолт — admin/operator.
Worker_bot НЕ получает доступа — для worker'а есть отдельный internal
`/internal/servers/{id}/ipmi/credentials`.
"""
from __future__ import annotations

import base64

import pytest

BASE = "/api/server/v1/ipmi-controllers"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestRevealCredentials:
    async def test_operator_reveals(
        self, client, operator_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(
            server_id=srv.id,
            username="bmc-admin",
            password="reveal-bmc-please",
        )
        resp = await client.post(
            f"{BASE}/{ctrl.id}/reveal-credentials",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"login", "password_b64"}
        assert body["login"] == "bmc-admin"
        decoded = base64.b64decode(body["password_b64"]).decode("utf-8")
        assert decoded == "reveal-bmc-please"
        # Plaintext не должен утечь в response незакодированным.
        assert "reveal-bmc-please" not in body["password_b64"]

    async def test_admin_reveals(
        self, client, admin_role_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(
            server_id=srv.id, username="root", password="dell-idrac-pw",
        )
        resp = await client.post(
            f"{BASE}/{ctrl.id}/reveal-credentials",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["login"] == "root"
        assert base64.b64decode(body["password_b64"]).decode() == "dell-idrac-pw"

    async def test_reader_cannot_reveal(
        self, client, reader_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{ctrl.id}/reveal-credentials",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_cross_dept_returns_404_hidden(
        self, client, operator_token_a, make_server, make_ipmi,
    ):
        """Cross-dept controller прячем за 404 (одинаково с несуществующим id)."""
        srv = await make_server(department_id="dep_b")
        ctrl = await make_ipmi(
            server_id=srv.id, username="other-dept", password="cross-dept",
        )
        resp = await client.post(
            f"{BASE}/{ctrl.id}/reveal-credentials",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "IPMI_CONTROLLER_NOT_FOUND"

    async def test_nonexistent_returns_404(self, client, operator_token_a):
        resp = await client.post(
            f"{BASE}/ipm_ghost_xx/reveal-credentials",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "IPMI_CONTROLLER_NOT_FOUND"

    async def test_audit_emit_on_success(
        self, client, operator_token_a, make_server, make_ipmi, monkeypatch,
    ):
        """`ipmi_controller.credentials_revealed` пишется со status=success
        и WARNING-семантикой (см. AUDIT_EVENTS.md). Plaintext в details не утекает."""
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
        resp = await client.post(
            f"{BASE}/{ctrl.id}/reveal-credentials",
            headers=_hdr(operator_token_a),
        )
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
        # Plaintext в details не пишем (audit_helpers redaction даже не нужен).
        assert "audit-leak-check" not in str(details)
        assert details.get("department_id") == "dep_a"
        assert details.get("server_id") == srv.id
        assert details.get("username") == "bmc-audit"

    async def test_broken_ciphertext_returns_500(
        self, client, operator_token_a, make_server, make_ipmi, db,
    ):
        """Сломанный ciphertext → AppException DECRYPT_FAILED (http 500)."""
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
        resp = await client.post(
            f"{BASE}/{ctrl.id}/reveal-credentials",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 500
        assert resp.json()["error_code"] == "DECRYPT_FAILED"

    @pytest.mark.parametrize("kind,vendor", [
        ("idrac", "idrac"),
        ("ilo", "ilo"),
        ("ipmi", "ipmi_generic"),
        ("redfish", "ipmi_generic"),
    ])
    async def test_vendor_variants_reveal_same_shape(
        self, client, operator_token_a, make_server, make_ipmi, db, kind, vendor,
    ):
        """Reveal-shape `{login, password_b64}` не зависит от vendor/kind BMC."""
        from sqlalchemy import update

        from src.models import IpmiController

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(
            server_id=srv.id, kind=kind, username=f"u_{kind}",
            password=f"pw_for_{vendor}",
        )
        # bmc_vendor у фикстуры может быть default'ом; явно обновим.
        await db.execute(
            update(IpmiController)
            .where(IpmiController.id == ctrl.id)
            .values(bmc_vendor=vendor)
        )
        await db.flush()
        resp = await client.post(
            f"{BASE}/{ctrl.id}/reveal-credentials",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["login"] == f"u_{kind}"
        assert base64.b64decode(body["password_b64"]).decode() == f"pw_for_{vendor}"
