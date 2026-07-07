"""Тесты `POST /api/server/v1/internal/servers/{id}/power-state` callback'а.

Воркер пишет результат живой пробы питания (`power.status`) обратно в кэш
сервера. До этого callback'а `servers.power_state` никогда не обновлялось —
UI всегда видел дефолтный unknown.

Одним callback'ом приходят три независимых сигнала — ping / ssh / ipmi. Legacy
power_state остаётся, но unknown его не затирает (anti-clobber).

Покрывается:
- happy path: power_state + source + checked_at пишутся в БД (worker_bot);
- unknown принимается;
- приём и сохранение трёх наборов сигналов (ping/ssh/ipmi);
- anti-clobber: unknown не перетирает закэшированное on/off, но пишет ping/ssh;
- back-compat: старый payload (только power_state/source) не трогает новые колонки;
- RBAC: worker_bot ок (грант prepare_callback), admin/reader — 403, без токена — 401;
- невалидные power_state / source → 422;
- несуществующий server_id → 404;
- ServerResponse отдаёт power_state_source / power_state_checked_at + ping/ssh/ipmi;
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

    async def test_three_signals_persisted(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        """Один callback с ping/ssh/ipmi — все три набора ложатся в свои колонки."""
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={
                "power_state": "on",
                "source": "bmc",
                "ping_reachable": True,
                "ping_latency_ms": 1.5,
                "ssh_reachable": True,
                "ssh_latency_ms": 12.0,
                "ipmi_power_state": "on",
            },
        )
        assert resp.status_code == 200, resp.text

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.ping_reachable is True
        assert refreshed.ping_latency_ms == 1.5
        assert refreshed.ping_checked_at is not None
        assert refreshed.ssh_reachable is True
        assert refreshed.ssh_latency_ms == 12.0
        assert refreshed.ssh_checked_at is not None
        assert refreshed.ipmi_power_state == "on"
        assert refreshed.ipmi_checked_at is not None
        # legacy-тройка тоже обновилась — power_state определённый.
        assert refreshed.power_state == "on"
        assert refreshed.power_state_source == "bmc"

    async def test_unknown_does_not_clobber_cached_power_state(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        """unknown-callback не затирает ранее закэшированное on/off, но пишет ping/ssh."""
        srv = await make_server(department_id=dept_a)
        # Первый callback: определённое состояние on по bmc.
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "on", "source": "bmc"},
        )
        # Второй callback: сводное unknown, но ping/ssh достучались.
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={
                "power_state": "unknown",
                "source": "bmc",
                "ping_reachable": True,
                "ping_latency_ms": 2.0,
                "ssh_reachable": False,
            },
        )
        assert resp.status_code == 200, resp.text
        # Ответ отдаёт закэшированное состояние, а не unknown.
        assert resp.json()["power_state"] == "on"

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        # legacy-тройка осталась прежней.
        assert refreshed.power_state == "on"
        assert refreshed.power_state_source == "bmc"
        # новые сигналы записаны.
        assert refreshed.ping_reachable is True
        assert refreshed.ping_latency_ms == 2.0
        assert refreshed.ssh_reachable is False
        assert refreshed.ssh_checked_at is not None

    async def test_legacy_payload_leaves_new_columns_untouched(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        """Старый payload (только power_state/source) не трогает ping/ssh/ipmi-колонки."""
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={"power_state": "on", "source": "ssh"},
        )
        assert resp.status_code == 200, resp.text

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.power_state == "on"
        assert refreshed.power_state_source == "ssh"
        # Новые колонки не заполнялись — сигналы не слались.
        assert refreshed.ping_reachable is None
        assert refreshed.ping_checked_at is None
        assert refreshed.ssh_reachable is None
        assert refreshed.ipmi_power_state is None
        assert refreshed.ipmi_checked_at is None

    async def test_server_response_exposes_split_signals(
        self, client, worker_bot_token_a, admin_role_token_a, make_server, dept_a,
    ):
        """Список/карточка сервера отдаёт ping/ssh/ipmi-поля."""
        srv = await make_server(department_id=dept_a)
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/power-state",
            headers=_hdr(worker_bot_token_a),
            json={
                "power_state": "on",
                "source": "ping",
                "ping_reachable": True,
                "ping_latency_ms": 3.25,
                "ssh_reachable": True,
                "ssh_latency_ms": 8.0,
                "ipmi_power_state": "on",
            },
        )
        resp = await client.get(f"{BASE_SRV}/{srv.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ping_reachable"] is True
        assert body["ping_latency_ms"] == 3.25
        assert body["ping_checked_at"] is not None
        assert body["ssh_reachable"] is True
        assert body["ssh_latency_ms"] == 8.0
        assert body["ssh_checked_at"] is not None
        assert body["ipmi_power_state"] == "on"
        assert body["ipmi_checked_at"] is not None
