"""Тесты `POST /servers/{id}/inventory/probe` — lightweight inventory dispatch.

Аналог `/inventory/sync` из worker_dispatch (full collection), но другой
task_kind = `inventory.probe`. BMC не нужен — probe идёт по SSH/ICMP.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1/servers"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task из endpoints/inventory.py."""
    calls: list[dict] = []
    by_key: dict[str, str] = {}

    async def fake_dispatch(*, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None):
        if idempotency_key is not None and idempotency_key in by_key:
            return by_key[idempotency_key]
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "created_by": created_by,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if idempotency_key is not None:
            by_key[idempotency_key] = new_id
        return new_id

    monkeypatch.setattr(
        "src.api.v1.endpoints.inventory.worker_client.dispatch_task", fake_dispatch,
    )
    return calls


@pytest.fixture
def captured_emits(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.inventory.audit_service.emit", fake_emit,
    )
    return captured


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


# ── Happy path ───────────────────────────────────────────────────────────────


class TestInventoryProbeDispatch:
    async def test_operator_dispatches_probe(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["task_id"].startswith("tsk_")
        assert body["status"] == "queued"
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["task_kind"] == "inventory.probe"
        assert captured_dispatch[0]["target_server_id"] == srv.id
        assert captured_dispatch[0]["payload"] == {
            "server_id": srv.id,
            "target_department_id": "dep_a",
        }

    async def test_admin_role_can_probe(
        self, client, admin_role_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202

    async def test_works_without_ipmi(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Probe идёт по SSH — отсутствие BMC не должно блокировать."""
        srv = await make_server(department_id="dep_a")  # без with_ipmi
        resp = await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202


# ── Permissions / visibility ─────────────────────────────────────────────────


class TestInventoryProbePermissions:
    async def test_reader_cannot_probe(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert captured_dispatch == []

    async def test_guest_cannot_probe(
        self, client, guest_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(guest_token_a),
        )
        assert resp.status_code == 403
        assert captured_dispatch == []

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(operator_token_b),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"
        assert captured_dispatch == []

    async def test_no_token_returns_401(self, client, make_server, captured_dispatch):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(f"{BASE}/{srv.id}/inventory/probe")
        assert resp.status_code == 401
        assert captured_dispatch == []

    async def test_nonexistent_server_returns_404(
        self, client, operator_token_a, captured_dispatch,
    ):
        resp = await client.post(
            f"{BASE}/srv_ghost_probe/inventory/probe", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert captured_dispatch == []


# ── Бизнес-валидации ─────────────────────────────────────────────────────────


class TestInventoryProbeBusinessRules:
    async def test_decommissioned_returns_409(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []

    async def test_idempotency_key_dedupes(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        h = {**_hdr(operator_token_a), "Idempotency-Key": "probe-key-1"}
        r1 = await client.post(f"{BASE}/{srv.id}/inventory/probe", headers=h)
        r2 = await client.post(f"{BASE}/{srv.id}/inventory/probe", headers=h)
        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r1.json()["task_id"] == r2.json()["task_id"]
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["idempotency_key"] == "probe-key-1"

    async def test_different_keys_create_different_tasks(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        r1 = await client.post(
            f"{BASE}/{srv.id}/inventory/probe",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "p-1"},
        )
        r2 = await client.post(
            f"{BASE}/{srv.id}/inventory/probe",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "p-2"},
        )
        assert r1.json()["task_id"] != r2.json()["task_id"]
        assert len(captured_dispatch) == 2


# ── Audit emission ───────────────────────────────────────────────────────────


class TestInventoryProbeAudit:
    async def test_success_emits_event(
        self, client, operator_token_a, make_server, captured_dispatch, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(operator_token_a),
        )
        successes = [e for e in _events(captured_emits, "server.inventory_probe") if e["status"] == "success"]
        assert len(successes) == 1
        assert successes[0]["target_id"] == srv.id
        assert successes[0]["details"]["task_kind"] == "inventory.probe"

    async def test_permission_denied_emits_denied(
        self, client, reader_token_a, make_server, captured_dispatch, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(reader_token_a),
        )
        denied = [e for e in _events(captured_emits, "server.inventory_probe") if e["status"] == "denied"]
        assert len(denied) == 1
        assert denied[0]["details"]["reason"] == "permission_denied"

    async def test_decommissioned_emits_failure(
        self, client, operator_token_a, make_server, captured_dispatch, captured_emits, db,
    ):
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        await client.post(
            f"{BASE}/{srv.id}/inventory/probe", headers=_hdr(operator_token_a),
        )
        failures = [e for e in _events(captured_emits, "server.inventory_probe") if e["status"] == "failure"]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "decommissioned"
