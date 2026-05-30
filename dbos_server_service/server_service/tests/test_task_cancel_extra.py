"""Дополнительные тесты cancel-endpoint'а, не вошедшие в test_task_cancel_endpoint.py.

Покрывает:
* task без target_server_id (heartbeat-like) — dept-isolation не применяется,
  только permission-check;
* worker-DB unavailable (ServiceUnavailableError из _fetch) → 503 без denied-аудита;
* task_id граничные значения: минимум (1 символ) и максимум (64 символа);
* task_id длиннее 64 символов → 422;
* race-ветка: _fetch нашёл row, cancel_task вернул found=False (row исчез между
  двумя вызовами) → 404 с denied-аудитом;
* admin cancels task без reason, previous_status в ответе корректен.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake_worker(monkeypatch):
    state: dict = {"tasks": {}, "cancel_calls": []}

    async def fake_fetch(task_id_value: str):
        return state["tasks"].get(task_id_value)

    async def fake_cancel(*, task_id_value, cancelled_by, cancel_reason):
        state["cancel_calls"].append({
            "task_id": task_id_value,
            "cancelled_by": cancelled_by,
            "cancel_reason": cancel_reason,
        })
        row = state["tasks"].get(task_id_value)
        if row is None:
            return {"found": False}
        prev = row["status"]
        if prev not in ("queued", "running"):
            return {"found": True, "cancelled": False, "previous_status": prev,
                    "task_kind": row["task_kind"], "target_server_id": row["target_server_id"]}
        row["status"] = "cancelled"
        return {"found": True, "cancelled": True, "previous_status": prev,
                "task_kind": row["task_kind"], "target_server_id": row["target_server_id"]}

    import src.api.v1.endpoints.tasks as tasks_endpoint
    monkeypatch.setattr(tasks_endpoint.worker_client, "_fetch_task_status_and_meta", fake_fetch)
    monkeypatch.setattr(tasks_endpoint.worker_client, "cancel_task", fake_cancel)
    return state


@pytest.fixture
def captured_audit(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr("src.api.v1.endpoints.tasks.audit_service.emit", fake_emit)
    monkeypatch.setattr("src.services.server.audit_service.emit", fake_emit)
    return captured


def _events(captured, action):
    return [e for e in captured if e["action"] == action]


class TestTaskCancelNoTargetServer:
    """Task без target_server_id — dept-isolation не применяется."""

    async def test_admin_cancels_heartbeat_task(
        self, client, admin_role_token_a, fake_worker, captured_audit,
    ):
        fake_worker["tasks"]["tsk_heartbeat_1"] = {
            "status": "queued",
            "target_server_id": None,
            "task_kind": "heartbeat",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_heartbeat_1/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_id"] == "tsk_heartbeat_1"
        assert body["previous_status"] == "queued"

        ev = _events(captured_audit, "task.cancelled")
        assert len(ev) == 1
        assert ev[0]["status"] == "success"
        assert ev[0]["details"]["target_server_id"] is None

    async def test_task_without_server_id_terminal_gives_409(
        self, client, admin_role_token_a, fake_worker,
    ):
        fake_worker["tasks"]["tsk_heartbeat_done"] = {
            "status": "succeeded",
            "target_server_id": None,
            "task_kind": "heartbeat",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_heartbeat_done/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "TASK_NOT_CANCELLABLE"


class TestTaskCancelServiceUnavailable:
    """_fetch_task_status_and_meta падает с ServiceUnavailableError → 503."""

    async def test_worker_db_unavailable_returns_503(
        self, client, admin_role_token_a, monkeypatch, captured_audit,
    ):
        from src.core.exceptions import ServiceUnavailableError

        async def unavailable_fetch(task_id_value: str):
            raise ServiceUnavailableError(
                error_code="WORKER_DB_NOT_CONFIGURED",
                message="not configured",
            )

        import src.api.v1.endpoints.tasks as tasks_endpoint
        monkeypatch.setattr(
            tasks_endpoint.worker_client, "_fetch_task_status_and_meta", unavailable_fetch,
        )

        resp = await client.post(
            f"{BASE}/tasks/tsk_unavailable/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 503
        # Denied-аудит НЕ должен появляться — это infra-сбой, не отказ доступа.
        denied = [e for e in captured_audit if e.get("status") == "denied"]
        assert denied == []


class TestTaskCancelIdValidation:
    """Граничные значения task_id."""

    async def test_task_id_single_char_accepted(
        self, client, admin_role_token_a, fake_worker,
    ):
        """task_id минимальной длины (1 символ) — 404 (задача не найдена)."""
        resp = await client.post(
            f"{BASE}/tasks/x/cancel",
            headers=_hdr(admin_role_token_a),
        )
        # Нет такой задачи → 404, но путь прошёл валидацию.
        assert resp.status_code == 404

    async def test_task_id_64_chars_accepted(
        self, client, admin_role_token_a, fake_worker,
    ):
        """task_id максимальной длины (64 символа) — 404."""
        task_id = "t" * 64
        resp = await client.post(
            f"{BASE}/tasks/{task_id}/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404

    async def test_task_id_65_chars_rejected(
        self, client, admin_role_token_a, fake_worker,
    ):
        """task_id длиннее 64 символов → 422 (path validation)."""
        task_id = "t" * 65
        resp = await client.post(
            f"{BASE}/tasks/{task_id}/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 422


class TestTaskCancelRaceCondition:
    """Row исчез между _fetch и cancel_task → 404."""

    async def test_row_vanishes_between_fetch_and_cancel(
        self, client, admin_role_token_a, make_server, monkeypatch, captured_audit,
    ):
        srv = await make_server(department_id="dep_a")

        # _fetch видит queued, cancel_task возвращает found=False (row удалён).
        async def fetch_sees_row(task_id_value: str):
            return {
                "status": "queued",
                "target_server_id": srv.id,
                "task_kind": "power.on",
            }

        async def cancel_row_gone(*, task_id_value, cancelled_by, cancel_reason):
            return {"found": False}

        import src.api.v1.endpoints.tasks as tasks_endpoint
        monkeypatch.setattr(
            tasks_endpoint.worker_client, "_fetch_task_status_and_meta", fetch_sees_row,
        )
        monkeypatch.setattr(
            tasks_endpoint.worker_client, "cancel_task", cancel_row_gone,
        )
        import src.services.audit_service as audit_mod

        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr("src.api.v1.endpoints.tasks.audit_service.emit", fake_emit)
        monkeypatch.setattr("src.services.server.audit_service.emit", fake_emit)

        resp = await client.post(
            f"{BASE}/tasks/tsk_race_gone/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TASK_NOT_FOUND"

        ev = [e for e in captured if e["action"] == "task.cancelled"]
        assert len(ev) == 1
        assert ev[0]["status"] == "denied"
        assert ev[0]["details"]["reason"] == "task_not_found"


class TestTaskCancelResponseShape:
    """Проверяем структуру ответа (все поля присутствуют)."""

    async def test_response_fields_complete(
        self, client, admin_role_token_a, make_server, fake_worker,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_shape_check"] = {
            "status": "running",
            "target_server_id": srv.id,
            "task_kind": "inventory.sync",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_shape_check/cancel",
            headers=_hdr(admin_role_token_a),
            json={"reason": "shape test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == "tsk_shape_check"
        assert body["status"] == "cancelled"
        assert body["previous_status"] == "running"
        assert body["cancelled_at"] is not None
        assert body["cancelled_by"] is not None
        assert body["cancel_reason"] == "shape test"

    async def test_empty_body_cancel_reason_is_none(
        self, client, admin_role_token_a, make_server, fake_worker,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_no_reason"] = {
            "status": "queued",
            "target_server_id": srv.id,
            "task_kind": "power.off",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_no_reason/cancel",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["cancel_reason"] is None

    async def test_reason_exactly_512_chars_accepted(
        self, client, admin_role_token_a, make_server, fake_worker,
    ):
        srv = await make_server(department_id="dep_a")
        fake_worker["tasks"]["tsk_max_reason"] = {
            "status": "queued",
            "target_server_id": srv.id,
            "task_kind": "power.on",
        }
        resp = await client.post(
            f"{BASE}/tasks/tsk_max_reason/cancel",
            headers=_hdr(admin_role_token_a),
            json={"reason": "a" * 512},
        )
        assert resp.status_code == 200
