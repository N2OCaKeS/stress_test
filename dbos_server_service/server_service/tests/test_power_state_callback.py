"""Тесты `POST /api/server/v1/internal/servers/{id}/power-state` callback'а.

Воркер пишет результат живой пробы питания (`power.status`) обратно в кэш
сервера. До этого callback'а `servers.power_state` никогда не обновлялось —
UI всегда видел дефолтный unknown.

Покрывается:
- happy path: power_state + source + checked_at пишутся в БД (worker_bot);
- unknown принимается;
- RBAC: worker_bot ок (грант prepare_callback), admin/reader — 403, без токена — 401;
- невалидные power_state / source → 422;
- несуществующий server_id → 404;
- ServerResponse отдаёт power_state_source / power_state_checked_at;
- audit `server.power_state_updated` (success).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.models import Server

from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402

BASE_INT = "/api/server/v1/internal"
BASE_SRV = "/api/server/v1/servers"


@pytest.fixture
def captured_emits(monkeypatch):
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.services.internal_service.audit_service.emit",
    )


def _by_action(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


@pytest.mark.usefixtures("soft_dept_mode")
class TestPowerStateCallback:
    async def test_worker_bot_updates_power_state(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "on", "source": "ssh"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["power_state"] == "on"
        assert body["checked_at"]

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.power_state == "on"
        assert refreshed.power_state_source == "ssh"
        assert refreshed.power_state_checked_at is not None

    async def test_unknown_state_accepted(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "unknown", "source": "bmc"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["power_state"] == "unknown"

    async def test_repeat_callback_overwrites_cache(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "on", "source": "ping"},
        )
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "off", "source": "bmc"},
        )
        assert resp.status_code == 200, resp.text
        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.power_state == "off"
        assert refreshed.power_state_source == "bmc"

    async def test_admin_role_forbidden(
        self, client, admin_role_token_a, make_server, dept_a,
    ):
        """prepare_callback — worker_bot-only грант; admin его не несёт."""
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(admin_role_token_a),
            json={"power_state": "on", "source": "ssh"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_reader_forbidden(
        self, client, reader_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(reader_token_a),
            json={"power_state": "on", "source": "ssh"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_returns_401(self, client, make_server, dept_a):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            json={"power_state": "on", "source": "ssh"},
        )
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_invalid_power_state_returns_422(
        self, client, worker_bot_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "booting", "source": "ssh"},
        )
        assert_error(resp, 422)

    async def test_invalid_source_returns_422(
        self, client, worker_bot_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "on", "source": "telepathy"},
        )
        assert_error(resp, 422)

    async def test_server_not_found_returns_404(
        self, client, worker_bot_token_a,
    ):
        resp = await client.post(
            f"{BASE_INT}/servers/srv_missing/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "on", "source": "ssh"},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_audit_power_state_updated_emitted(
        self, client, worker_bot_token_a, make_server, captured_emits, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "on", "source": "bmc"},
        )
        events = _by_action(captured_emits, "server.power_state_updated")
        success = [e for e in events if e.get("status") == "success"]
        assert success, f"expected success emit, got {events}"
        assert success[0].get("target_id") == srv.id
        assert success[0]["details"].get("power_state") == "on"
        assert success[0]["details"].get("source") == "bmc"

    async def test_server_response_exposes_new_fields(
        self, client, worker_bot_token_a, admin_role_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "off", "source": "ping"},
        )
        resp = await client.get(f"{BASE_SRV}/{srv.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["power_state"] == "off"
        assert body["power_state_source"] == "ping"
        assert body["power_state_checked_at"] is not None
