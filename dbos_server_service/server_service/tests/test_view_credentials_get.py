"""Тесты для пользовательских read-only IPMI-эндпоинтов.

* `GET /servers/{id}/ipmi/credentials` — метаданные controller'а (БЕЗ
  plaintext-пароля).
* `GET /servers/{id}/ipmi/power` — кэшированный power_state из server.power_state.

Реальный PostgreSQL через сервисный docker-compose.test.yml (см. conftest).
"""

from __future__ import annotations

import pytest  # noqa: F401  — re-exported transitively by conftest fixtures

BASE = "/api/server/v1/servers"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── GET /ipmi/credentials (метаданные) ───────────────────────────────────────


class TestViewCredentialsMetadata:
    async def test_admin_sees_metadata_without_password(
        self, client, admin_token, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, username="ipmi_admin")
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/credentials", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["server_id"] == srv.id
        assert body["username"] == "ipmi_admin"
        assert body["endpoint_url"]
        assert body["kind"]
        # Plaintext-пароль НЕ должен светиться нигде.
        assert "password" not in body
        assert "ipmi-plaintext-secret" not in resp.text

    async def test_reader_without_credentials_grant_returns_403(
        self, client, reader_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/credentials", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_operator_without_credentials_grant_returns_403(
        self, client, operator_token_a, make_server, make_ipmi,
    ):
        """`operator` имеет CRUD на ipmi, но НЕ view_credentials."""
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/credentials", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 403

    async def test_worker_bot_can_view_metadata(
        self, client, worker_bot_token_a, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/credentials", headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200
        assert "password" not in resp.json()

    async def test_no_ipmi_returns_404(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")  # без IPMI controller
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/credentials", headers=_hdr(admin_token),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "IPMI_NOT_FOUND"

    async def test_cross_dept_returns_404(
        self, client, admin_token_b, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/credentials", headers=_hdr(admin_token_b),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(f"{BASE}/{srv.id}/ipmi/credentials")
        assert resp.status_code == 401


# ── GET /ipmi/power (кэшированный) ───────────────────────────────────────────


class TestPowerStatusCached:
    async def test_returns_cached_power_state(
        self, client, reader_token_a, make_server, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.power_state = "on"
        await db.flush()
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/power", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["server_id"] == srv.id
        assert body["power_state"] == "on"

    async def test_unknown_state_default(
        self, client, reader_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")  # default power_state=unknown
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/power", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["power_state"] == "unknown"

    async def test_returns_last_probed_at_when_ipmi_present(
        self, client, reader_token_a, make_server, make_ipmi, db,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        from datetime import datetime, timezone
        ctrl.last_probed_at = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        await db.flush()
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/power", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["last_probed_at"] is not None
        assert "2026-04-01" in resp.json()["last_probed_at"]

    async def test_no_ipmi_last_probed_is_null(
        self, client, reader_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/power", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["last_probed_at"] is None

    async def test_guest_cannot_view_power_status(
        self, client, guest_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/power", headers=_hdr(guest_token_a),
        )
        # guest по дефолту не имеет power_status в матрице
        assert resp.status_code == 403

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            f"{BASE}/{srv.id}/ipmi/power", headers=_hdr(operator_token_b),
        )
        assert resp.status_code == 404

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(f"{BASE}/{srv.id}/ipmi/power")
        assert resp.status_code == 401


