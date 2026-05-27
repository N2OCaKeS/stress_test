"""Тесты бутстрапа управления сервером (#14).

Покрывает:
* trigger-dispatch `POST /servers/{id}/prepare` — 202 + task_id, право
  (update), base64-декод кред в payload, битый base64 → 422, decommissioned;
* callback `POST /internal/servers/{id}/prepared` — помечает сервер
  `is_managed`/`prepared_at`/`management_user`; worker_bot может, reader нет;
* bootstrap-креды не уходят в audit.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from src.core.constants import ServerStatus
from src.models import Server

BASE = "/api/server/v1/servers"
BASE_INT = "/api/server/v1/internal"


def _hdr(token: str, dept: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if dept is not None:
        headers["X-Target-Department-Id"] = dept
    return headers


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task из endpoints/worker_dispatch.py.

    Возвращает список dispatch-вызовов. Каждый элемент дополнительно несёт
    `stored_creds` — креды, которые prepare положил бы в Redis под ключ из
    payload (мокаем `store_prepare_creds`, чтобы тест не ходил в Redis).
    """
    calls: list[dict] = []
    stored: dict[str, dict] = {}

    async def fake_store(creds_key, creds):
        stored[creds_key] = creds

    async def fake_dispatch(*, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None):
        creds_key = payload.get("bootstrap_creds_key")
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "stored_creds": stored.get(creds_key) if creds_key else None,
        })
        return f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "store_prepare_creds", fake_store)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.store_prepare_creds",
        fake_store,
    )
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    return calls


# ── Trigger: POST /servers/{id}/prepare ──────────────────────────────────────


class TestPrepareDispatch:
    async def test_operator_prepares(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234")},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"].startswith("tsk_")
        assert body["status"] == "queued"
        assert len(captured_dispatch) == 1
        call = captured_dispatch[0]
        assert call["task_kind"] == "server.prepare"
        # Plaintext-кред в payload НЕТ — только ссылка на Redis-ключ.
        assert "bootstrap_login" not in call["payload"]
        assert "bootstrap_password" not in call["payload"]
        creds_key = call["payload"]["bootstrap_creds_key"]
        assert creds_key.startswith("dbos:prepare_creds:")
        assert call["payload"]["target_department_id"] == "dep_a"
        # base64 декодирован и креды ушли в Redis-store под этот ключ.
        assert call["stored_creds"] == {
            "bootstrap_login": "bootadmin",
            "bootstrap_password": "Boot1234",
        }

    async def test_reader_cannot_prepare(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(reader_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234")},
        )
        assert resp.status_code == 403, resp.text
        assert captured_dispatch == []

    async def test_bad_base64_username_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": "!!notb64!!", "password_b64": _b64("Boot1234")},
        )
        assert resp.status_code == 422, resp.text
        assert captured_dispatch == []

    async def test_bad_base64_password_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": "@@bad@@"},
        )
        assert resp.status_code == 422, resp.text
        assert captured_dispatch == []

    async def test_cross_dept_server_404(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_b),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234")},
        )
        assert resp.status_code == 404, resp.text
        assert captured_dispatch == []

    async def test_decommissioned_409(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234")},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []


# ── Callback: POST /internal/servers/{id}/prepared ───────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestPreparedCallback:
    async def test_marks_server_managed(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        assert srv.is_managed is False

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/prepared",
            headers=_hdr(worker_bot_token_a),
            json={"management_user": "dbos"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_managed"] is True
        assert body["prepared_at"] is not None

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.is_managed is True
        assert refreshed.management_user == "dbos"
        assert refreshed.prepared_at is not None

    async def test_idempotent_repeat(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        for _ in range(2):
            resp = await client.post(
                f"{BASE_INT}/servers/{srv.id}/prepared",
                headers=_hdr(worker_bot_token_a),
                json={"management_user": "dbos"},
            )
            assert resp.status_code == 200, resp.text

    async def test_reader_cannot_callback(
        self, client, reader_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/prepared",
            headers=_hdr(reader_token_a),
            json={"management_user": "dbos"},
        )
        assert resp.status_code == 403, resp.text

    async def test_unknown_server_404(
        self, client, worker_bot_token_a, dept_a,
    ):
        resp = await client.post(
            f"{BASE_INT}/servers/srv_does_not_exist/prepared",
            headers=_hdr(worker_bot_token_a),
            json={"management_user": "dbos"},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"


# ── Audit: bootstrap-креды не уходят в журнал ────────────────────────────────


class TestPrepareAuditSafety:
    async def test_credentials_not_in_audit(
        self, client, operator_token_a, make_server, captured_dispatch, monkeypatch,
    ):
        events: list[dict] = []

        def fake_emit(action, **kwargs):
            events.append({"action": action, **kwargs})

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit", fake_emit,
        )
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234")},
        )
        assert resp.status_code == 202, resp.text
        blob = str(events)
        assert "Boot1234" not in blob
        assert "bootadmin" not in blob
