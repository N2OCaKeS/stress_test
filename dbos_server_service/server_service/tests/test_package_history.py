"""Тесты `GET /api/server/v1/servers/{id}/packages/history` — история запросов пакетов.

Endpoint server-scoped: отдаёт прошлые `installed_packages.list`-задачи сервера
(см. `endpoints/installed_packages.py::list_packages_history`). Cross-DB read из
`dev_server_worker.tasks` мочится через monkeypatch — на стороне server_service
проверяем permission `(server, view)` -> dept-visibility -> маппинг row→
PackageHistoryEntry (pattern из payload, packages из result) -> X-Total-Count.

Серверы (с реальными department_id) создаются через `make_server`, чтобы
dept-isolation (`server_svc.get_server`) работала по настоящим row'ам.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1"


def _url(server_id: str) -> str:
    return f"{BASE}/servers/{server_id}/packages/history"


def _hist_row(
    *,
    id: str,
    status: str = "succeeded",
    payload: dict | None = None,
    result=None,
    created_by: str | None = None,
    last_error: str | None = None,
):
    """Row истории (как отдаёт `worker_client.list_package_history`)."""
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    terminal = status in ("succeeded", "failed", "cancelled")
    return {
        "id": id,
        "status": status,
        "payload": payload,
        "result": result,
        "enqueued_at": now,
        "completed_at": now if terminal else None,
        "created_by": created_by,
        "last_error": last_error,
    }


@pytest.fixture
def fake_package_history(monkeypatch):
    """Перехват `worker_client.list_package_history`.

    Тест кладёт row'ы в `state["rows"]` (порядок = тот, что отдаёт реальный
    запрос, т.е. уже `enqueued_at DESC`). Фейк применяет limit/offset и считает
    total (или берёт явный `state["total"]`), записывает аргументы в `calls`.
    """
    state: dict = {"rows": [], "total": None, "calls": []}

    async def fake(*, server_id, limit, offset):
        state["calls"].append(
            {"server_id": server_id, "limit": limit, "offset": offset}
        )
        rows = list(state["rows"])
        total = state["total"] if state["total"] is not None else len(rows)
        return rows[offset : offset + limit], total

    from src.services import worker_client

    monkeypatch.setattr(worker_client, "list_package_history", fake)
    return state


class TestHistoryHappy:
    async def test_returns_entries_with_pattern_and_packages(
        self, client, operator_token_a, make_server, fake_package_history,
    ):
        srv = await make_server(department_id="dep_a")
        fake_package_history["rows"] = [
            _hist_row(
                id="pkg_2",
                status="succeeded",
                payload={"pattern": "linux-image*", "patterns": ["linux-image*"]},
                result={"packages": [{"name": "linux-image-6", "version": "6.1"}]},
                created_by="usr_other",
            ),
            _hist_row(
                id="pkg_1",
                status="succeeded",
                payload={"pattern": "vim", "patterns": ["vim"]},
                result={"packages": [{"name": "vim", "version": "9"},
                                     {"name": "vim-common", "version": "9"}]},
                created_by="usr_me",
            ),
        ]
        resp = await client.get(_url(srv.id), headers=_hdr(operator_token_a))
        assert resp.status_code == 200, resp.text
        assert resp.headers["X-Total-Count"] == "2"
        body = resp.json()
        # Порядок сквозной — как отдал worker (enqueued_at DESC), свежие сверху.
        assert [e["task_id"] for e in body] == ["pkg_2", "pkg_1"]
        first = body[0]
        assert first["status"] == "succeeded"
        assert first["pattern"] == "linux-image*"
        assert first["patterns"] == ["linux-image*"]
        assert first["requested_by"] == "usr_other"
        assert first["package_count"] == 1
        assert first["packages"] == [{"name": "linux-image-6", "version": "6.1"}]
        assert "requested_at" in first
        second = body[1]
        assert second["package_count"] == 2

    async def test_forwards_server_and_pagination(
        self, client, operator_token_a, make_server, fake_package_history,
    ):
        srv = await make_server(department_id="dep_a")
        fake_package_history["rows"] = [
            _hist_row(id=f"pkg_{i}") for i in range(5)
        ]
        resp = await client.get(
            _url(srv.id) + "?limit=2&offset=1", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["X-Total-Count"] == "5"
        assert len(resp.json()) == 2
        call = fake_package_history["calls"][-1]
        assert call == {"server_id": srv.id, "limit": 2, "offset": 1}

    async def test_running_request_has_empty_packages(
        self, client, operator_token_a, make_server, fake_package_history,
    ):
        srv = await make_server(department_id="dep_a")
        fake_package_history["rows"] = [
            _hist_row(id="pkg_run", status="running", payload={"pattern": "*"}),
        ]
        resp = await client.get(_url(srv.id), headers=_hdr(operator_token_a))
        assert resp.status_code == 200, resp.text
        entry = resp.json()[0]
        assert entry["status"] == "running"
        assert entry["packages"] is None
        assert entry["package_count"] is None
        assert entry["finished_at"] is None

    async def test_failed_request_carries_error(
        self, client, operator_token_a, make_server, fake_package_history,
    ):
        srv = await make_server(department_id="dep_a")
        fake_package_history["rows"] = [
            _hist_row(
                id="pkg_fail", status="failed", payload={"pattern": "*"},
                last_error="SERVER_MANAGEMENT_AUTH_FAILED",
            ),
        ]
        resp = await client.get(_url(srv.id), headers=_hdr(operator_token_a))
        assert resp.status_code == 200, resp.text
        entry = resp.json()[0]
        assert entry["status"] == "failed"
        assert entry["last_error"] == "SERVER_MANAGEMENT_AUTH_FAILED"

    async def test_empty_history(
        self, client, operator_token_a, make_server, fake_package_history,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(_url(srv.id), headers=_hdr(operator_token_a))
        assert resp.status_code == 200, resp.text
        assert resp.headers["X-Total-Count"] == "0"
        assert resp.json() == []

    async def test_reader_sees_all_requests_not_only_own(
        self, client, reader_token_a, make_server, fake_package_history,
    ):
        """История server-scoped: reader видит запросы любых инициаторов."""
        srv = await make_server(department_id="dep_a")
        fake_package_history["rows"] = [
            _hist_row(id="pkg_x", payload={"pattern": "*"}, created_by="usr_someone_else"),
        ]
        resp = await client.get(_url(srv.id), headers=_hdr(reader_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 1
        assert body[0]["requested_by"] == "usr_someone_else"


class TestHistoryAccess:
    async def test_guest_without_view_denied(
        self, client, guest_token_a, make_server, fake_package_history,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(_url(srv.id), headers=_hdr(guest_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")
        # Permission отбит до cross-DB read'а.
        assert fake_package_history["calls"] == []

    async def test_cross_dept_server_hidden_404(
        self, client, operator_token_a, make_server, fake_package_history,
    ):
        srv_b = await make_server(department_id="dep_b")
        resp = await client.get(_url(srv_b.id), headers=_hdr(operator_token_a))
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert fake_package_history["calls"] == []

    async def test_missing_server_404(
        self, client, operator_token_a, fake_package_history,
    ):
        resp = await client.get(_url("srv_does_not_exist"), headers=_hdr(operator_token_a))
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert fake_package_history["calls"] == []
